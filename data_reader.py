import pandas as pd
import numpy as np
import itertools
import pyedflib
from os import path
from util_funcs import read_config, get_abs_files, get_annotation_types, get_data_split, get_reference_node_types, COMMON_DELTA, np_rolling_window
import multiprocessing as mp
from pathos.multiprocessing import Pool
import argparse
import pickle as pkl

# to allow us to load data in without dealing with resource issues
# https://stanford.edu/~shervine/blog/keras-how-to-generate-data-on-the-fly
class MultiProcessingDataset():
    """Class to help improve speed of looking up multiple records at once using multiple processes.
            Just make this the parent class, then call the getItemSlice method on slice objects
    """
    def getItemSlice(self, i):
        toReturn = []
        manager = mp.Manager()
        inQ = manager.Queue()
        outQ = manager.Queue()
        [inQ.put(j) for j in range(*i.indices(len(self)))]
        [inQ.put(None) for j in range(self.n_process)]
        processes = [mp.Process(target=self.helper_process, args=(inQ, outQ)) for j in range(self.n_process)]
        print("Starting {} processes".format(self.n_process))
        [p.start() for p in processes]
        [p.join() for p in processes]
        while not outQ.empty():
            index, res = outQ.get()
            #NOTE: some EDF files fail to read, so accessing them from queue will fail with large slices
            toReturn.append(res) #no guarantee of order unfortunately...
        # toReturn.sort(key=lambda x: return x[0])
        return toReturn
        # return Pool().map(self.__getitem__, toReturn)

    def helper_process(self, in_q, out_q):
        for i in iter(in_q.get, None):
            # if i%10 == 0:
            print("retrieving: {}".format(i))
            out_q.put((i, self[i]))

class EdfFFTDatasetTransformer(MultiProcessingDataset):
    freq_bins = [0.2 * i for i in range(50)] + list(range(10, 50, 1)) + list(range(50,400, 20))
    """Implements an indexable dataset applying fft to entire timeseries,
        returning histogram bins of fft frequencies

    Parameters
    ----------
    edf_dataset : EdfDataset
        an array-like returning the raw channel data and the output as a tuple
    freq_bins : type
        Description of parameter `freq_bins`.
    n_process : type
        Description of parameter `n_process`.
    precache : type
        Description of parameter `precache`.
    window_size : type
        Description of parameter `window_size`.
    non_overlapping : type
        Description of parameter `non_overlapping`.

    """
    def __init__(
        self,
        edf_dataset,
        freq_bins = EdfFFTDatasetTransformer.freq_bins,
        n_process=None,
        precache=False,
        window_size=None,
        non_overlapping=True):
        """Used to read the raw data in

        Parameters
        ----------
        edf_dataset : EdfDataset
            Array-like returning the channel data (channel by time) and annotations (doesn't matter what the shape is)
        freq_bins : array
            Used to segment the frequencies into histogram bins
        n_process : int
            Used to define the number of processes to use for large reads in. If None, uses cpu count
        precache : bool
            Use to load all data at beginning and keep cache of it during operations
        window_size : pd.Timedelta
            If None, runs the FFT on the entire datset. If set, uses overlapping windows to run fft on
        non_overlapping : bool

        Returns
        -------
        None

        """
        self.edf_dataset = edf_dataset
        if n_process is None:
            n_process = mp.cpu_count()
        self.n_process = n_process
        self.precache = False
        self.freq_bins = freq_bins
        self.window_size = window_size
        if precache:
            print("starting precache job with: {} processes".format(self.n_process))
            self.data = self[:]
        self.precache = precache
        self.non_overlapping = non_overlapping

    def __len__(self):
        return len(self.edf_dataset)


    def __getitem__(self, i):
        if self.precache:
            return self.data[i]
        if type(i) == slice:
            return self.getItemSlice(i)
        if self.window_size == None:
            original_data = self.edf_dataset[i]
            fft_data = np.nan_to_num(np.abs(np.fft.fft(original_data[0].values, axis=0)))
            fft_freq = np.fft.fftfreq(fft_data.shape[0], d=COMMON_DELTA)
            fft_freq_bins = self.freq_bins
            new_fft_hist = pd.DataFrame(index=fft_freq_bins[:-1], columns=original_data[0].columns)
            for i, name in enumerate(original_data[0].columns):
                new_fft_hist[name] = np.histogram(fft_freq, bins=fft_freq_bins, weights=fft_data[:,i])[0]
            return new_fft_hist, original_data[1]
        else:
            window_count_size = int(self.window_size / pd.Timedelta(seconds=COMMON_DELTA))
            original_data = self.edf_dataset[i]
            fft_data = np.nan_to_num(np.abs(np.fft.fft(original_data[0].values, axis=0)))
            fft_data_windows = np_rolling_window(np.array(fft_data.T), window_count_size)
            if self.non_overlapping:
                fft_data_windows = fft_data_windows[:,list(range(0, fft_data_windows.shape[1], window_count_size))]

            fft_data = np.abs(
                np.fft.fft(
                    fft_data_windows,
                    axis=2)) #channel, window num, frequencies
            fft_freq_bins = self.freq_bins
            new_hist_bins = np.zeros((fft_data.shape[0], fft_data.shape[1], len(fft_freq_bins) - 1))
            fft_freq = np.fft.fftfreq(window_count_size, d=COMMON_DELTA)
            for i, channel in enumerate(fft_data):
                for j, window_channel in enumerate(channel):
                    new_hist_bins[i, j, :] = np.histogram(fft_freq, bins=fft_freq_bins, weights=window_channel)[0]
            if (self.edf_dataset.expand_tse and not self.non_overlapping):
                return new_hist_bins, original_data[1].rolling(window_count_size).mean()[:-window_count_size + 1]
            elif (self.edf_dataset.expand_tse and self.non_overlapping):
                annotations = original_data[1].rolling(window_count_size).mean()[:-window_count_size + 1]
                return new_hist_bins, annotations.iloc[list(range(0, annotations.shape[0], window_count_size))]
            else:
                return new_hist_bins, original_data[1]



class EdfDataset(MultiProcessingDataset):
    """Short summary.

    Parameters
    ----------
    data_split : type
        Description of parameter `data_split`.
    ref : type
        Description of parameter `ref`.
    resample : type
        Description of parameter `resample`.

    Attributes
    ----------
    manager : multiprocessing.Manager
        used to manage multiprocessing. TODO: not implemented
    edf_tokens : list
        a list of edf file paths to consider, assumes a corresponding tse file
        exists
    n_process : int
        When indexing by slice, use multiprocessing to speed up execution
    data_split : str
    ref : str
    resample : pd.Timedelta

    """
    def __init__(self, data_split, ref, num_files=None, resample=pd.Timedelta(seconds=COMMON_DELTA), expand_tse=True, n_process=None):
        self.data_split = data_split
        if n_process is None:
            n_process = mp.cpu_count()
        self.n_process = n_process
        self.ref = ref
        self.resample = resample
        self.manager = mp.Manager()
        self.edf_tokens = get_all_token_file_names(data_split, ref)
        self.expand_tse = expand_tse
        if num_files is not None:
            self.edf_tokens = self.edf_tokens[0:num_files]
    def __len__(self):
        return len(self.edf_tokens)

    def __getitem__(self, i):
        if type(i) == slice:
            return self.getItemSlice(i)
        return get_edf_data_and_label_ts_format(self.edf_tokens[i], resample=self.resample, expand_tse=self.expand_tse)
    #
    # def get_data_runner(to_get_queue, to_return_queue):
    #     for edf_path in iter(to_get_queue.get, None):
    #         to_return_queue = ()
    # def get_data_multiprocess():


def get_edf_data_and_label_ts_format(edf_path, expand_tse=True, resample=pd.Timedelta(seconds=COMMON_DELTA)):
    edf_data = edf_eeg_2_df(edf_path, resample)
    tse_data_path = convert_edf_path_to_tse(edf_path)
    if expand_tse:
        tse_data_ts = read_tse_file_and_return_ts(tse_data_path, edf_data.index)
    else:
        tse_data_ts = read_tse_file(tse_data_path)
    return edf_data, tse_data_ts

def read_tse_file(tse_path):
    tse_data_lines = []
    with open(tse_path, 'r') as f:
        for line in f:
            if "#" in line:
                continue #this is a comment
            elif "version" in line:
                assert "tse_v1.0.0" in line #assert file is correct version
            elif len(line.strip()) == 0:
                continue #Just blank space, continue
            else:
                line = line.strip()
                subparts = line.split()
                tse_data_line = pd.Series(index=['start', 'end', 'label', 'p'])
                tse_data_line['start'] = float(subparts[0])
                tse_data_line['end'] = float(subparts[1])
                tse_data_line['label'] = str(subparts[2])
                tse_data_line['p'] = float(subparts[3])
                tse_data_lines.append(tse_data_line)
    tse_data = pd.concat(tse_data_lines, axis=1).T
    return tse_data

def convert_edf_path_to_tse(edf_path):
    return edf_path[:-4] + ".tse"

def read_tse_file_and_return_ts(tse_path, ts_index):
    ann_y = read_tse_file(tse_path)

    return expand_tse_file(ann_y, ts_index)


def expand_tse_file(ann_y, ts_index):
    ann_y_t = pd.DataFrame(columns=get_annotation_types(), index=ts_index)
    ann_y.apply(lambda row: ann_y_t[row['label']].loc[pd.Timedelta(seconds=row['start']):pd.Timedelta(seconds=row['end'])].fillna(row['p'], inplace=True), axis=1)
    ann_y_t.fillna(0, inplace=True)
    return ann_y_t


def edf_eeg_2_df(path, resample=None):
    """ Transforms from EDF to pd.df, with channel labels as columns.
        This does not attempt to concatenate multiple time series but only takes
        a single edf filepath

    Parameters
    ----------
    path : str
        path of the edf file

    resample : pd.Timedelta
        if None, returns original data with original sampling
        otherwise, resamples to correct Timedelta using forward filling

    Returns
    -------
    pd.DataFrame
        index is time, columns is waveform channel label

    """
    reader = pyedflib.EdfReader(path)
    channel_names = [headerDict['label'] for headerDict in reader.getSignalHeaders()]
    sample_rates = [headerDict['sample_rate'] for headerDict in reader.getSignalHeaders()]
    start_time = reader.getStartdatetime()
    all_channels = []
    for i, channel_name in enumerate(channel_names):
        signal_data = reader.readSignal(i)
        signal_data = pd.Series(
            signal_data,
            index=pd.date_range(
                start=start_time,
                freq=pd.Timedelta(seconds=1/sample_rates[i]),
                periods=len(signal_data)
                ),
            name=channel_name
            )
        all_channels.append(signal_data)
    data = pd.concat(all_channels, axis=1)
    data.index = data.index - data.index[0]
    if resample != None:
        data = data.resample(resample).ffill()
    return data

def get_patient_dir_names(data_split, ref, full_path=True):
    """ Gets the path names of the folders holding the patient info

    Parameters
    ----------
    data_split : str
        Which data split the patient belongs to
    ref : str
        which reference voltage system that is used
    full_path : bool
        Whether or not to return abs path

    Returns
    -------
    list
        list of strings describing path

    """
    assert data_split in get_data_split()
    assert ref in get_reference_node_types()
    root_dir_entry = data_split + "_" + ref
    config = read_config()
    root_dir_path = config[root_dir_entry]
    subdirs = get_abs_files(root_dir_path)
    patient_dirs = list(itertools.chain.from_iterable([get_abs_files(subdir) for subdir in subdirs]))
    if full_path:
        return patient_dirs
    else:
        return [path.basename(patient_dir) for patient_dir in patient_dirs]

def get_session_dir_names(data_split, ref, full_path=True, patient_dirs=None):
    """Gets the path names of the folders holding the session info
        (patients can have multiple eeg sessions)

    Parameters
    ----------
    data_split : str
        Which data split the patient belongs to
    ref : str
        which reference voltage system that is used
    full_path : bool
        Whether or not to return abs path

    Returns
    -------
    list
        list of strings describing path
    """
    assert data_split in get_data_split()
    assert ref in get_reference_node_types()
    if patient_dirs is None:
        patient_dirs = get_patient_dir_names(data_split, ref)
    session_dirs = list(itertools.chain.from_iterable([get_abs_files(patient_dir) for patient_dir in patient_dirs]))
    if full_path:
        return session_dirs
    else:
        return [path.basename(session_dir) for session_dir in session_dirs]

def get_all_token_file_names(data_split, ref, full_path=True):
    session_dirs = get_session_dir_names(data_split, ref)
    token_fns = list(itertools.chain.from_iterable([get_token_file_names(session_dir) for session_dir in session_dirs]))
    if full_path:
        return token_fns
    else:
        return [path.basename(token_fn) for token_fn in token_fns]


def get_token_file_names(session_dir_path, full_path=True):
    sess_file_names = get_abs_files(session_dir_path)
    time_series_fns = [fn for fn in sess_file_names if fn[-4:] == '.edf']
    if full_path:
        return time_series_fns
    else:
        return [path.basename(fn) for fn in time_series_fns]

def get_session_data(session_dir_path):
    time_series_fns = get_token_file_names(session_dir_path)
    signal_dfs = []
    for fn in time_series_fns:
        signal_dfs.append(edf_eeg_2_df(fn))
    return pd.concat(signal_dfs)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Holds utility functions for reading data. As a script, stores a copy of the dataset as pkl format')
    parser.add_argument("data_split", type=str)
    parser.add_argument("ref", type=str)
    parser.add_argument("--path", type=str, default="")
    parser.add_argument("--num_files", type=int, default=None)
    args = parser.parse_args()
    edf_dataset = EdfFFTDatasetTransformer(EdfDataset(args.data_split, args.ref, num_files=args.num_files, expand_tse=False), precache=True)
    pkl.dump(edf_dataset.data, open(args.path +  "{}_{}_fft.pkl".format(args.data_split, args.ref), 'wb'))
