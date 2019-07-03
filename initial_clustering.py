import pickle as pkl
import util_funcs
from sklearn.cluster import MiniBatchKMeans
import scipy.cluster as cluster
import numpy as np
import pandas as pd
import data_reader as read
from sklearn.decomposition import PCA, FastICA
from sklearn import metrics
from sacred.observers import MongoObserver
import sacred
ex = sacred.Experiment("EEG_K-Means_dim_red_Clustering")


ex.observers.append(MongoObserver.create(client=util_funcs.get_mongo_client()))


@ex.named_config
def debug_config():
    num_eegs = 20


@ex.config
def config():
    num_comps = 6
    num_clusters = 5
    num_eegs = None
    num_cores = None
    data_split = "dev_test"
    ref = "01_tcp_ar"
    precached_pkl = None
    num_jobs = 1
    test_pkl = None
    dim_red = "pca"


@ex.capture
def get_data(num_eegs, data_split, ref, precached_pkl):
    """Returns the data to process.

    Parameters
    ----------
    num_eegs : type
        Description of parameter `num_eegs`.
    data_split : type
        Description of parameter `data_split`.
    ref : type
        Description of parameter `ref`.

    Returns
    -------
    tuple
        first elem is data freq coefficients of (num_instances, num_freq_bins),
        second elem is annotation percentages of (num_instances, annotations)

    """
    if precached_pkl is None:
        edf_dataset = read.EdfDataset(data_split, ref, num_files=num_eegs)
        fft_reader = read.EdfFFTDatasetTransformer(
            edf_dataset=edf_dataset,
            precache=True
        )
        data = fft_reader[0:num_eegs]
        annotations = np.array([datum[1].mean().values for datum in data])
    else:
        data = pkl.load(open(precached_pkl, 'rb'))[0:num_eegs]
        annotations = []
        for i in range(len(data)):
            annotations.append(
                read.expand_tse_file(
                    data[i][1],
                    pd.Series(list(range(int(data[i][1].end.max()))))
                    * pd.Timedelta(seconds=1)).mean().values)
        annotations = np.array(annotations)
    cols = [set(datum[0].dropna().columns) for datum in data]
    common_cols = list(cols[0].intersection(*cols))
    x_data = np.array([datum[0][common_cols].values for datum in data])
    x_data = x_data.reshape(x_data.shape[0], -1)
    return x_data, annotations


@ex.main
def main(num_comps, num_clusters, num_jobs, dim_red):
    data, annotations = get_data()
    if dim_red == 'pca':
        dim_red = PCA(num_comps)
    elif dim_red == 'ica':
        dim_red = FastICA(num_comps)
    dim_red.fit(data)
    dim_red_vects = dim_red.transform(data)
    kmeans = MiniBatchKMeans(num_clusters)
    cluster_pct = kmeans.fit_transform(cluster.vq.whiten(dim_red_vects))
    cluster_pct = np.apply_along_axis(
        lambda x: x / x.sum(), axis=1, arr=cluster_pct)
    nmi_score = metrics.normalized_mutual_info_score(
        np.argmax(
            annotations, axis=1), np.argmax(
            cluster_pct, axis=1))
    rand_score = metrics.adjusted_rand_score(
        np.argmax(
            annotations, axis=1), np.argmax(
            cluster_pct, axis=1))
    h_score = metrics.homogeneity_score(
        np.argmax(
            annotations, axis=1), np.argmax(
            cluster_pct, axis=1))
    c_score = metrics.completeness_score(
        np.argmax(
            annotations, axis=1), np.argmax(
            cluster_pct, axis=1))
    v_score = metrics.v_measure_score(
        np.argmax(
            annotations, axis=1), np.argmax(
            cluster_pct, axis=1))

    percent_y_per_cluster = pd.DataFrame(
        annotations.T @ cluster_pct,
        index=util_funcs.get_annotation_types())
    percent_y_per_cluster = (
        percent_y_per_cluster.T /
        percent_y_per_cluster.sum(
            axis=1)).fillna(0)
    print(percent_y_per_cluster)
    return {
        'dim_red': dim_red,
        'kmeans': kmeans,
        'cluster_pct': cluster_pct,
        'percent_y_per_cluster': percent_y_per_cluster,
        'nmi_score': nmi_score,
        'rand_score': rand_score,
        'h_score': h_score,
        'v_score': v_score,
        'c_score': c_score
    }


if __name__ == '__main__':
    ex.run_commandline()
