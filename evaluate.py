#!/usr/bin/env python
"""A program to calculate the false-alarm rate as well as the sensitive
distance from a search algorithm. (Part of the MLGWSC-1)
"""
import argparse
import numpy as np
import h5py
import os
import logging
from tqdm import tqdm


def find_injection_times(fgfiles, injfile, padding_start=0, padding_end=0):
    """Determine injections which are contained in the file.
    
    Arguments
    ---------
    fgfiles : list of str
        Paths to the files containing the foreground data (noise +
        injections).
    injfile : str
        Path to the file containing information on the injections in the
        foreground files.
    padding_start : {float, 0}
        The amount of time (in seconds) at the start of each segment
        where no injections are present.
    padding_end : {float, 0}
        The amount of time (in seconds) at the end of each segment
        where no injections are present.
    
    Returns
    -------
    duration:
        A float representing the total duration (in seconds) of all
        foreground files.
    bool-indices:
        A 1D array containing bools that specify which injections are
        contained in the provided foreground files.
    """
    duration = 0
    times = []
    for fpath in fgfiles:
        with h5py.File(fpath, 'r') as fp:
            det = list(fp.keys())[0]
            
            for key in fp[det].keys():
                ds = fp[f'{det}/{key}']
                start = ds.attrs['start_time']
                end = start + len(ds) * ds.attrs['delta_t']
                duration += end - start
                start += padding_start
                end -= padding_end
                if end > start:
                    times.append([start, end])
    
    with h5py.File(injfile, 'r') as fp:
        injtimes = fp['tc'][()]
    
    ret = np.full((len(times), len(injtimes)), False)
    for i, (start, end) in enumerate(times):
        ret[i] = np.logical_and(start <= injtimes, injtimes <= end)
    
    return duration, np.any(ret, axis=0)


def find_closest_index(array, value, assume_sorted=False):
    """Find the index of the closest element in the array for the given
    value(s).
    
    Arguments
    ---------
    array : np.array
        1D numpy array.
    value : number or np.array
        The value(s) of which the closest array element should be found.
    assume_sorted : {bool, False}
        Assume that the array is sorted. May improve evaluation speed.
    
    Returns
    -------
    indices:
        Array of indices. The length is determined by the length of
        value. Each index specifies the element in array that is closest
        to the value at the same position.
    """
    if len(array) == 0:
        raise ValueError('Cannot find closest index for empty input array.')
    if not assume_sorted:
        array = array.copy()
        array.sort()
    ridxs = np.searchsorted(array, value, side='right')
    lidxs = np.maximum(ridxs - 1, 0)
    comp = np.fabs(array[lidxs] - value) < \
           np.fabs(array[np.minimum(ridxs, len(array) - 1)] - value)  # noqa: E127, E501
    lisbetter = np.logical_or((ridxs == len(array)), comp)
    ridxs[lisbetter] -= 1
    return ridxs


def mchirp(mass1, mass2):
    return (mass1 * mass2) ** (3. / 5.) / (mass1 + mass2) ** (1. / 5.)


def get_stats(fgevents, bgevents, injparams, duration=None,
              chirp_distance=False):
    """Calculate the false-alarm rate and sensitivity of a search
    algorithm.
    
    Arguments
    ---------
    fgevents : np.array
        A numpy array with three rows. The first row has to contain the
        times returned by the search algorithm where it believes to have
        found a true signal. The second row contains a ranking statistic
        like quantity for each time. The third row contains the maxmimum
        distance to an injection for the given event to be counted as
        true positive. The values have to be determined on the
        foreground data, i.e. noise plus additive signals.
    bgevents : np.array
        A numpy array with three rows. The first row has to contain the
        times returned by the search algorithm where it believes to have
        found a true signal. The second row contains a ranking statistic
        like quantity for each time. The third row contains the maxmimum
        distance to an injection for the given event to be counted as
        true positive. The values have to be determined on the
        background data, i.e. pure noise.
    injparams : dict
        A dictionary containing at least two entries with keys `tc` and
        `distance`. Both entries have to be numpy arrays of the same
        length. The entry `tc` contains the times at which injections
        were made in the foreground. The entry `distance` contains the
        according luminosity distances of these injections.
    duration : {None or float, None}
        The duration of the analyzed background. If None the injections
        are used to infer the duration.
    
    Returns
    -------
    dict:
        Returns a dictionary, where each key-value pair specifies some
        statistic. The most important are the keys `far` and
        `sensitive-distance`.
    """
    ret = {}
    injtimes = injparams['tc']
    dist = injparams['distance']
    if chirp_distance:
        massc = mchirp(injparams['mass1'], injparams['mass2'])
    if duration is None:
        duration = injtimes.max() - injtimes.min()
    logging.info('Sorting foreground event times')
    sidxs = fgevents[0].argsort()
    fgevents = fgevents.T[sidxs].T
    
    logging.info('Finding injection times closest to event times')
    idxs = find_closest_index(injtimes, fgevents[0])
    # print(idxs)
    diff = np.abs(injtimes[idxs] - fgevents[0])
    
    logging.info('Finding true- and false-positives')
    tpbidxs = diff <= fgevents[2]
    tpidxs = np.arange(len(fgevents[0]))[tpbidxs]
    fpbidxs = diff > fgevents[2]
    fpidxs = np.arange(len(fgevents[0]))[fpbidxs]
    
    tpevents = fgevents.T[tpidxs].T
    fpevents = fgevents.T[fpidxs].T
    
    ret['fg-events'] = fgevents
    ret['found-indices'] = np.arange(len(injtimes))[idxs]
    ret['missed-indices'] = np.setdiff1d(np.arange(len(injtimes)),
                                         ret['found-indices'])
    ret['true-positive-event-indices'] = tpidxs
    ret['false-positive-event-indices'] = fpidxs
    ret['sorting-indices'] = sidxs
    ret['true-positive-diffs'] = diff[tpidxs]
    ret['false-positive-diffs'] = diff[fpidxs]
    ret['true-positives'] = tpevents
    ret['false-positives'] = fpevents
    
    # Calculate foreground FAR
    logging.info('Calculating foreground FAR')
    noise_stats = fpevents[1].copy()
    noise_stats.sort()
    fgfar = len(noise_stats) - np.arange(len(noise_stats)) - 1
    fgfar = fgfar / duration
    ret['fg-far'] = fgfar
    
    # Calculate background FAR
    logging.info('Calculating background FAR')
    noise_stats = bgevents[1].copy()
    noise_stats.sort()
    far = len(noise_stats) - np.arange(len(noise_stats)) - 1
    far = far / duration
    ret['far'] = far
    
    # Find best true-positive for each injection
    verbose = logging.root.level is logging.INFO
    found_injections = []
    tmpsidxs = idxs.argsort()
    sorted_idxs = idxs[tmpsidxs]
    iidxs = np.full(len(idxs), False)
    for i in tqdm(range(len(injtimes)), ascii=True, disable=not verbose,
                  desc='Determining found injections'):
        L = np.searchsorted(sorted_idxs, i, side='left')
        if L >= len(idxs) or sorted_idxs[L] != i:
            continue
        R = np.searchsorted(sorted_idxs, i, side='right')
        # All indices that point to the same injection
        iidxs[tmpsidxs[L:R]] = True
        # Indices of the true-positives that belong to the same injection
        eidxs = np.logical_and(iidxs[tmpsidxs[L:R]],
                               tpbidxs[tmpsidxs[L:R]])  
        if eidxs.any():
            found_injections.append([i,
                                    np.max(fgevents[1][tmpsidxs[L:R]][eidxs])])
        iidxs[tmpsidxs[L:R]] = False
    
    found_injections = np.array(found_injections).T
    
    # Calculate sensitivity
    # CARE! THIS APPLIES ONLY WHEN THE DISTRIBUTION IS CHOSEN CORRECTLY
    logging.info('Calculating sensitivity')
    sidxs = found_injections[1].argsort()
    found_injections = found_injections.T[sidxs].T  # Sort found injections
    if chirp_distance:
        found_mchirp_total = massc[found_injections[0].astype(int)]
        mchirp_max = massc.max()
    max_distance = dist.max()
    vtot = (4. / 3.) * np.pi * max_distance**3.
    Ninj = len(dist)
    if chirp_distance:
        mc_norm = mchirp_max ** (5. / 2.) * len(massc)
    else:
        mc_norm = Ninj
    prefactor = vtot / mc_norm
    
    nfound = len(found_injections[1]) - np.searchsorted(found_injections[1],
                                                        noise_stats,
                                                        side='right')
    if chirp_distance:
        # Get found chirp-mass indices for given threshold
        fidxs = np.searchsorted(found_injections[1], noise_stats, side='right')
        found_mchirp_total = np.flip(found_mchirp_total)
        
        # Calculate sum(found_mchirp ** (5/2))
        # with found_mchirp = found_mchirp_total[i:]
        # and i looped over fidxs
        # Code below is a vectorized form of that
        cumsum = np.flip(np.cumsum(found_mchirp_total ** (5./2.)))
        cumsum = np.concatenate([cumsum, np.zeros(1)])
        mc_sum = cumsum[fidxs]
        Ninj = np.sum((mchirp_max / massc) ** (5. / 2.))
        
        cumsumsq = np.flip(np.cumsum(found_mchirp_total ** 5))
        cumsumsq = np.concatenate([cumsumsq, np.zeros(1)])
        sample_variance_prefactor = cumsumsq[fidxs]
        sample_variance = sample_variance_prefactor / Ninj\
                          - (mc_sum / Ninj) ** 2  # noqa: E127
    else:
        mc_sum = nfound
        sample_variance = nfound / Ninj - (nfound / Ninj) ** 2
    vol = prefactor * mc_sum
    vol_err = prefactor * (Ninj * sample_variance) ** 0.5
    rad = (3 * vol / (4 * np.pi))**(1. / 3.)
    
    ret['sensitive-volume'] = vol
    ret['sensitive-distance'] = rad
    ret['sensitive-volume-error'] = vol_err
    ret['sensitive-fraction'] = nfound / Ninj
        
    return ret


def main(doc):
    parser = argparse.ArgumentParser(description=doc)
    
    parser.add_argument('--injection-file', type=str, required=True,
                        help=("Path to the file containing information "
                              "on the injections. (The file returned by"
                              "`generate_data.py --output-injection-file`"))
    parser.add_argument('--foreground-events', type=str, nargs='+',
                        required=True,
                        help=("Path to the file containing the events "
                              "returned by the search on the foreground "
                              "data set as returned by "
                              "`generate_data.py --output-foreground-file`."))
    parser.add_argument('--foreground-files', type=str, nargs='+',
                        required=True,
                        help=("Path to the file containing the analyzed "
                              "foreground data output by"
                              "`generate_data.py --output-foreground-file`."))
    parser.add_argument('--background-events', type=str, nargs='+',
                        required=True,
                        help=("Path to the file containing the events "
                              "returned by the search on the background"
                              "data set as returned by "
                              "`generate_data.py --output-background-file`."))
    parser.add_argument('--output-file', type=str, required=True,
                        help=("Path at which to store the output HDF5 "
                              "file. (Path must end in `.hdf`)"))
    
    parser.add_argument('--verbose', action='store_true',
                        help="Print update messages.")
    parser.add_argument('--force', action='store_true',
                        help="Overwrite existing files.")
    
    args = parser.parse_args()
    
    # Setup logging
    log_level = logging.INFO if args.verbose else logging.WARN
    logging.basicConfig(format='%(levelname)s | %(asctime)s: %(message)s',
                        level=log_level, datefmt='%d-%m-%Y %H:%M:%S')
    
    # Sanity check arguments here
    if os.path.splitext(args.output_file)[1] != '.hdf':
        raise ValueError('The output file must have the extension `.hdf`.')
    
    if os.path.isfile(args.output_file) and not args.force:
        raise IOError(f'The file {args.output_file} already exists. '
                      'Set the flag `force` to overwrite it.')
    
    # Find indices contained in foreground file
    logging.info('Finding injections contained in data')
    padding_start, padding_end = 30, 30
    dur, idxs = find_injection_times(args.foreground_files,
                                     args.injection_file,
                                     padding_start=padding_start,
                                     padding_end=padding_end)
    if np.sum(idxs) == 0:
        msg = 'The foreground data contains no injections! '
        msg += 'Probably a too small section of data was generated. '
        msg += 'Please make sure to generate at least {} seconds of data. '
        msg += 'Otherwise a sensitive distance cannot be calculated.'
        msg = msg.format(padding_start + padding_end + 24)
        raise RuntimeError(msg)
    
    # Read injection parameters
    logging.info(f'Reading injections from {args.injection_file}')
    injparams = {}
    with h5py.File(args.injection_file, 'r') as fp:
        injparams['tc'] = fp['tc'][()][idxs]
        injparams['distance'] = fp['distance'][()][idxs]
        injparams['mass1'] = fp['mass1'][()][idxs]
        injparams['mass2'] = fp['mass2'][()][idxs]
        use_chirp_distance = 'chirp_distance' in fp.keys()
    
    # Read foreground events
    logging.info(f'Reading foreground events from {args.foreground_events}')
    fg_events = []
    for fpath in args.foreground_events:
        with h5py.File(fpath, 'r') as fp:
            fg_events.append(np.vstack([fp['time'],
                                        fp['stat'],
                                        fp['var']]))
    fg_events = np.concatenate(fg_events, axis=-1)
    
    # Read background events
    logging.info(f'Reading background events from {args.background_events}')
    bg_events = []
    for fpath in args.background_events:
        with h5py.File(fpath, 'r') as fp:
            bg_events.append(np.vstack([fp['time'],
                                        fp['stat'],
                                        fp['var']]))
    bg_events = np.concatenate(bg_events, axis=-1)
    
    stats = get_stats(fg_events, bg_events, injparams,
                      duration=dur,
                      chirp_distance=use_chirp_distance)
    
    # Store results
    logging.info(f'Writing output to {args.output_file}')
    mode = 'w' if args.force else 'x'
    with h5py.File(args.output_file, mode) as fp:
        for key, val in stats.items():
            fp.create_dataset(key, data=np.array(val))
    return


if __name__ == "__main__":
    main(__doc__)
