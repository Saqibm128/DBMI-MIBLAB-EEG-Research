from sacred.observers import MongoObserver
import pickle as pkl
from addict import Dict
from sklearn.pipeline import Pipeline
import clinical_text_analysis as cta
import pandas as pd
import numpy as np
from os import path
import data_reader as read
import constants
import util_funcs
from sklearn.model_selection import PredefinedSplit, GridSearchCV
from sklearn.metrics import f1_score, make_scorer, accuracy_score, roc_auc_score, matthews_corrcoef
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
import wf_analysis.datasets as wfdata
from keras_models.dataGen import EdfDataGenerator
from keras_models.vanPutten import vp_conv2d, conv2d_gridsearch, inception_like
from keras import optimizers
import pickle as pkl
import sacred
import keras
import ensembleReader as er
from keras.utils import multi_gpu_model

import random
import string
from keras.callbacks import ModelCheckpoint, EarlyStopping
ex = sacred.Experiment(name="predict_seizure_in_eeg")

# ex.observers.append(MongoObserver.create(client=util_funcs.get_mongo_client()))





@ex.named_config
def debug():
    num_files = 200
    batch_size = 16
    num_epochs = 20
    max_num_samples = 2



@ex.named_config
def standardized_ensemble():
    use_random_ensemble = True
    precached_pkl = "/n/scratch2/ms994/standardized_simple_ensemble_train_data_seizure.pkl"
    precached_test_pkl = "/n/scratch2/ms994/standardized_simple_ensemble_test_data_seizure.pkl"
    ensemble_sample_info_path = "/n/scratch2/ms994/standardized_edf_ensemble_sample_info_seizure.pkl"

    max_num_samples = 40  # number of samples of eeg data segments per eeg.edf file
    use_standard_scaler = True
    use_filtering = True


@ex.named_config
def stop_on_training_loss():
    early_stopping_on = "loss"

@ex.config
def config():
    train_split = "train"
    test_split = "dev_test"
    ref = "01_tcp_ar"
    n_process = 8
    num_files = None
    max_length = 4 * constants.COMMON_FREQ
    batch_size = 64
    start_offset_seconds = 0  # matters if we aren't doing random ensemble sampling
    dropout = 0.25
    use_early_stopping = True
    patience = 10
    model_name = randomString() + ".h5"
    precached_pkl = "train_data.pkl"
    precached_test_pkl = "test_data.pkl"
    num_epochs = 1000
    lr = 0.0002
    validation_size = 0.2
    test_size = 0.2
    use_cached_pkl = True
    use_vp = True
    # for custom architectures
    num_conv_spatial_layers = 4
    num_conv_temporal_layers = 1
    conv_spatial_filter = (3, 3)
    num_spatial_filter = 100
    conv_temporal_filter = (2, 5)
    num_temporal_filter = 1
    use_filtering = True
    max_pool_size = (1, 3)
    max_pool_stride = (1, 2)
    use_batch_normalization = True
    use_random_ensemble = False
    max_num_samples = 10  # number of samples of eeg data segments per eeg.edf file
    num_gpus = 1
    early_stopping_on = "val_loss"
    test_train_split_pkl_path = "train_test_split_info.pkl"
    # if use_cached_pkl is false and this is true, just generates pickle files, doesn't make models or anything
    regenerate_data = False
    use_standard_scaler = False
    ensemble_sample_info_path = "edf_ensemble_path.pkl"
    fit_generator_verbosity = 2
    steps_per_epoch = None
    validation_steps = None
    shuffle_generator = True
    use_dl = True
    use_inception_like=False


# https://pynative.com/python-generate-random-string/
def randomString(stringLength=16):
    """Generate a random string of fixed length """
    letters = string.ascii_uppercase
    return ''.join(random.choice(letters) for i in range(stringLength))


@ex.capture
def get_model_checkpoint(model_name, monitor='val_loss'):
    return ModelCheckpoint(model_name, monitor=monitor, save_best_only=True, verbose=1)


@ex.capture
def get_early_stopping(patience, early_stopping_on):
    return EarlyStopping(patience=patience, verbose=1, monitor=early_stopping_on)


@ex.capture
def get_cb_list(use_early_stopping, model_name):
    if use_early_stopping:
        return [get_early_stopping(), get_model_checkpoint(), get_model_checkpoint("bin_acc_"+model_name, "val_binary_accuracy")]
    else:
        return [get_model_checkpoint(), get_model_checkpoint("bin_acc_"+model_name, "val_binary_accuracy")]

@ex.capture
def get_data_generator(split, pkl_file, shuffle_generator, use_cached_pkl_dataset=True):
    if use_cached_pkl_dataset and path.exists(pkl_file):
        edf = pkl.load(open(pkl_file, 'rb'))
    else:
        edf = EdfDataGenerator(get_base_dataset(split=split), precache=True, shuffle=shuffle_generator)
        pkl.dump(edf, open(pkl_file, 'wb'))
    return edf

@ex.capture
def get_base_dataset(split,
                     ref,
                     n_process,
                     num_files,
                     max_num_samples,
                     max_length,
                     start_offset_seconds,
                     ensemble_sample_info_path,
                     use_standard_scaler,
                     use_filtering,
                     use_cached_pkl_dataset=True,
                     is_test=False,
                     is_valid=False):
    edfData = er.EdfDatasetEnsembler(
        split,
        ref,
        n_process=n_process,
        max_length=max_length *
        pd.Timedelta(seconds=constants.COMMON_DELTA),
        use_numpy=True,
        num_files=num_files,
        max_num_samples=max_num_samples,
        filter=use_filtering
    )
    labels = read.SeizureLabelReader(sampleInfo=edfData.sampleInfo, n_process=n_process)
    labels.self_assign_to_sample_info(convert_to_int=True)

    samplingInfo = edfData.sampleInfo
    if use_standard_scaler:
        edfData = read.EdfStandardScaler(
            edfData, dataset_includes_label=True, n_process=n_process)
    ensemble_sample_info_path = "test_" + \
        ensemble_sample_info_path if is_test else ensemble_sample_info_path
    ensemble_sample_info_path = "valid_" + \
        ensemble_sample_info_path if is_valid else ensemble_sample_info_path
    if use_cached_pkl_dataset and path.exists(ensemble_sample_info_path):
        edfData.sampleInfo = pkl.load(
            open(ensemble_sample_info_path, 'rb'))
        ex.add_resource(ensemble_sample_info_path)
    else:
        pkl.dump(samplingInfo, open(ensemble_sample_info_path, 'wb'))
        ex.add_artifact(ensemble_sample_info_path)
    edfData.verbosity = 50
    return edfData


@ex.capture
def get_model(dropout, max_length, lr, use_vp, num_spatial_filter, use_batch_normalization, max_pool_size, use_inception_like, output_activation="softmax", num_gpus=1, num_outputs=2):
    if use_vp:
        model = vp_conv2d(
            dropout=dropout,
            input_shape=(21, max_length, 1),
            filter_size=num_spatial_filter,
            max_pool_size=max_pool_size,
            use_batch_normalization=use_batch_normalization,
            output_activation=output_activation,
            num_outputs=num_outputs
            )

    elif use_inception_like:
        model = get_inception_like(
            output_activation=output_activation,
            num_outputs=num_outputs)
    else:
        model = get_custom_model(
            output_activation=output_activation,
            num_outputs=num_outputs)
    if num_gpus != 1:
        model = multi_gpu_model(model, num_gpus)
    adam = optimizers.Adam(lr=lr)
    model.compile(adam, loss="categorical_crossentropy",
                  metrics=["binary_accuracy"])
    return model

@ex.capture
def get_inception_like(max_length, num_conv_spatial_layers, num_spatial_filter, dropout, lr, output_activation='softmax', num_outputs=2):
    model = inception_like((21, max_length, 1), num_layers=num_conv_spatial_layers, num_filters=num_spatial_filter, dropout=dropout, output_activation=output_activation, num_outputs=num_outputs)
    adam = optimizers.Adam(lr=lr)
    model.compile(adam, loss="categorical_crossentropy",
                  metrics=["binary_accuracy"])
    return model


@ex.capture
def get_custom_model(
    dropout,
    max_length,
    lr,
    use_batch_normalization,
    num_conv_spatial_layers=1,
    num_conv_temporal_layers=1,
    conv_spatial_filter=(3, 3),
    num_spatial_filter=100,
    conv_temporal_filter=(2, 5),
    num_temporal_filter=300,
    max_pool_size=(2, 2),
    max_pool_stride=(1, 2),
    output_activation='softmax',
    num_outputs=2
):
    model = conv2d_gridsearch(
        dropout=dropout,
        input_shape=(21, max_length, 1),
        num_conv_spatial_layers=num_conv_spatial_layers,
        num_conv_temporal_layers=num_conv_temporal_layers,
        conv_spatial_filter=conv_spatial_filter,
        num_spatial_filter=num_spatial_filter,
        conv_temporal_filter=conv_temporal_filter,
        num_temporal_filter=num_temporal_filter,
        max_pool_size=max_pool_size,
        max_pool_stride=max_pool_stride,
        use_batch_normalization=use_batch_normalization,
        output_activation=output_activation,
        num_outputs=num_outputs
    )
    adam = optimizers.Adam(lr=lr)
    model.compile(adam, loss="categorical_crossentropy",
                  metrics=["binary_accuracy"])
    return model



@ex.main
def main(use_dl):
    if use_dl:
        return dl()  # deep learning branch

@ex.capture
def get_train_valid_generator(train_split, precached_pkl):
    return get_data_generator(train_split, pkl_file=precached_pkl)

@ex.capture
def get_test_generator(test_split, precached_test_pkl):
    return get_data_generator(test_split, pkl_file=precached_test_pkl, shuffle_generator=False)

@ex.capture
def dl(train_split, test_split, num_epochs, lr, n_process, validation_size, max_length, use_random_ensemble, ref, num_files, regenerate_data, model_name, use_standard_scaler, fit_generator_verbosity, validation_steps, steps_per_epoch, num_gpus):
    trainValidationDataGenerator = get_train_valid_generator()
    testDataGenerator = get_test_generator()
    trainDataGenerator, validDataGenerator = trainValidationDataGenerator.create_validation_train_split(validation_size)

    model = get_model()
    history = model.fit_generator(
        trainDataGenerator,
        validation_steps=validation_steps,
        steps_per_epoch=steps_per_epoch,
        validation_data = validDataGenerator,
        verbose=fit_generator_verbosity,
        cb=get_cb_list()
        )

    y_pred = model.predict(np.stack(testData.dataset).reshape(0,2,1,3)) #time second, feature first
    print("pred shape", y_pred.shape)
    print("test data shape", testData.shape)

    auc = roc_auc_score(testGender, y_pred.argmax(axis=1))
    f1 = f1_score(testGender, y_pred.argmax(axis=1))
    accuracy = accuracy_score(testGender, y_pred.argmax(axis=1))

    y_pred_bin_acc = bin_acc_model.predict(testData)
    print("pred shape", y_pred_bin_acc.shape)
    print("test data shape", testData.shape)

    bin_acc_auc = roc_auc_score(testGender, y_pred_bin_acc.argmax(axis=1))
    bin_acc_f1 = f1_score(testGender, y_pred_bin_acc.argmax(axis=1))
    bin_acc_accuracy = accuracy_score(
        testGender, y_pred_bin_acc.argmax(axis=1))

    toReturn = {
        'history': history.history,
        'val_scores': {
            'min_val_loss': min(history.history['val_loss']),
            'max_val_acc': max(history.history['val_binary_accuracy']),
        },
        'test_scores': {
            'f1': f1,
            'acc': accuracy,
            'auc': auc
        },
        'best_bin_acc_test_scores':  {
            'f1': bin_acc_f1,
            'acc': bin_acc_accuracy,
            'auc': bin_acc_auc
        }}

    if use_standard_scaler:
        testGender = testEdfEnsembler.dataset.getEnsembledLabels()
    else:
        testGender = testEdfEnsembler.getEnsembledLabels()

    pred = np.round(average_pred)
    toReturn["ensemble_score"] = {
        "equal_vote":{},
        "over_all":{}
    }
    toReturn["ensemble_score"]["auc"] = roc_auc_score(label, pred) #keep auc,etc. here as well in top level for compatibility reasons when comparing
    toReturn["ensemble_score"]["acc"] = accuracy_score(label, pred)
    toReturn["ensemble_score"]["f1_score"] = f1_score(label, pred)
    toReturn["ensemble_score"]["discordance"] = np.abs(
        pred - average_pred).mean()
    toReturn["ensemble_score"]["equal_vote"]["auc"] = roc_auc_score(label, pred) #keep auc here as well in top level for compatibility reasons when comparing
    toReturn["ensemble_score"]["equal_vote"]["acc"] = accuracy_score(label, pred)
    toReturn["ensemble_score"]["equal_vote"]["f1_score"] = f1_score(label, pred)
    toReturn["ensemble_score"]["equal_vote"]["discordance"] = np.abs(
        pred - average_pred).mean()
    pred = np.round(average_over_all_pred)
    toReturn["ensemble_score"]["over_all"]["auc"] = roc_auc_score(label, pred) #keep auc here as well in top level for compatibility reasons when comparing
    toReturn["ensemble_score"]["over_all"]["acc"] = accuracy_score(label, pred)
    toReturn["ensemble_score"]["over_all"]["f1_score"] = f1_score(label, pred)
    toReturn["ensemble_score"]["over_all"]["discordance"] = np.abs(
        pred - average_pred).mean()
    return toReturn



if __name__ == "__main__":
    ex.run_commandline()