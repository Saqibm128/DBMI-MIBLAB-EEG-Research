import pickle as pkl
import json
import os
import os.path as path
import pandas as pd
import numpy as np
import pymongo
import itertools
import pyedflib
from sacred.serializer import restore  # to return a stored sacred result back
import multiprocessing as mp
import queue
import constants

# to allow us to load data in without dealing with resource issues
# https://stanford.edu/~shervine/blog/keras-how-to-generate-data-on-the-fly


class MultiProcessingDataset():
    """Class to help improve speed of looking up multiple records at once using multiple processes.
            Just make this the parent class, then call the getItemSlice method on slice objects
    """

    def getItemSlice(self, i):
        toReturn = [j for j in range(*i.indices(len(self)))]
        manager = mp.Manager()
        inQ = manager.Queue()
        outQ = manager.Queue()
        [inQ.put(j) for j in range(*i.indices(len(self)))]
        # [inQ.put(None) for j in range(self.n_process)]
        processes = [
            mp.Process(
                target=self.helper_process,
                args=(
                    inQ,
                    outQ)) for j in range(
                self.n_process)]
        print("Starting {} processes".format(self.n_process))
        [p.start() for p in processes]
        [p.join() for p in processes]
        startIndex = toReturn[0]
        while not outQ.empty():
            place, res = outQ.get()
            index = place - startIndex
            if type(res) == int:
                res = self[place]
            toReturn[index] = res
        return toReturn
        # return Pool().map(self.__getitem__, toReturn)

    def helper_process(self, in_q, out_q):
        try: #wait for blocking exception
            while True:
                i = in_q.get(block=True, timeout=1)
                if i % 5 == 0:
                    print("retrieving: {}".format(i))
                try:
                    out_q.put((i, self[i]))
                except Exception as e:
                    print(e, "Could not do {}, doing later".format(i))
                    in_q.put(i) #retry later
        except queue.Empty:
            print("Process completed")
            return


def np_rolling_window(a, window):
    # https://stackoverflow.com/questions/6811183/rolling-window-for-1d-arrays-in-numpy
    shape = a.shape[:-1] + (a.shape[-1] - window + 1, window)
    strides = a.strides + (a.strides[-1],)
    return np.lib.stride_tricks.as_strided(a, shape=shape, strides=strides)


def get_sacred_runs():
    return get_mongo_client().sacred.runs


def get_sacred_results(params):
    return restore(get_sacred_runs().find_one(params)['result'])


def get_abs_files(root_dir_path):
    """helper func to return full path names. helps with nested structure of
        extracted files

    Parameters
    ----------
    root_dir_path : type
        Description of parameter `root_dir_path`.

    Returns
    -------
    list
        Full paths of files, including directories, inside the root_dir_path
        If root_dir_path is a file and not a directory, this will fail

    """
    subdirs = os.listdir(root_dir_path)
    subdirs = [path.join(root_dir_path, subdir) for subdir in subdirs]
    return subdirs


cached_channel_names = None


def get_common_channel_names():
    global cached_channel_names
    if cached_channel_names is None:
        cached_channel_names = list(
            pd.read_csv(
                "channel_names.csv",
                header=None)[1])
    return cached_channel_names


cached_annotation_csv = None


def get_annotation_csv():
    global cached_annotation_csv
    if cached_annotation_csv is None:
        cached_annotation_csv = pd.read_csv(
            "data_labels.csv",
            header=0,
            dtype=str,
            keep_default_na=False,
        )
    return cached_annotation_csv


def get_annotation_types():
    """Used to get the specific annotation types. These are specified in
            .tse files and label specific time subsequences of the entire record
            These are also used in .lbl files to label time sequences of single channels

    Parameters
    ----------


    Returns
    -------
    list
        list of the lower case annotation codes

    """
    # https://www.isip.piconepress.com/projects/tuh_eeg/downloads/tuh_eeg_seizure/v1.5.0/_DOCS/
    return get_annotation_csv()["class_code"].str.lower().tolist()


def get_data_split():
    return ["train", "dev_test"]


def get_reference_node_types():
    """The TUH dataset is further split based on what was the reference voltage
        See: https://www.isip.piconepress.com/publications/conference_proceedings/2016/ieee_spmb/montages/

    Parameters
    ----------


    Returns
    -------
    list
        strings representing the appropriate subdirectory that describes
        reference
    """
    return ["01_tcp_ar", "02_tcp_le", "03_tcp_ar_a"]


def get_mongo_client(path="config.json"):
    '''
    Used for Sacred to record results
    '''
    config = read_config(path)
    if "mongo_uri" not in config.keys():
        return pymongo.MongoClient()
    else:
        mongo_uri = config["mongo_uri"]
        return pymongo.MongoClient(mongo_uri)


def read_config(path="config.json"):
    return json.load(open(path, "rb"))


if __name__ == "__main__":
    print(read_config())
    print(get_annotation_types())
    print('spsw' in get_annotation_types())
