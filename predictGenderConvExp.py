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
from sklearn.model_selection import GridSearchCV
from sklearn.metrics import f1_score, make_scorer, accuracy_score, roc_auc_score, matthews_corrcoef
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
import wf_analysis.datasets as wfdata
from keras_models.dataGen import EdfDataGenerator
from keras_models.vanPutten import vp_conv2d, conv2d_gridsearch
from keras import optimizers
import pickle as pkl
import sacred
import keras

import random, string
from keras.callbacks import ModelCheckpoint, EarlyStopping
ex = sacred.Experiment(name="gender_predict_conv_gridsearch")

from sacred.observers import MongoObserver
# ex.observers.append(MongoObserver.create(client=util_funcs.get_mongo_client()))

# trainEdfEnsembler = None
# testEdfEnsembler = None


@ex.named_config
def conv_spatial_filter_2_2():
    conv_spatial_filter = (2,2)

@ex.named_config
def conv_spatial_filter_3_3():
    conv_spatial_filter = (3,3)

@ex.named_config
def conv_temporal_filter_1_7():
    conv_temporal_filter=(1,7)

@ex.named_config
def conv_temporal_filter_2_3():
    conv_temporal_filter=(2,3)

@ex.named_config
def debug():
    num_files=200
    batch_size=16
    num_epochs=20
    max_num_samples=2

@ex.named_config
def extra_data_simple_ensemble_samples():
    '''
    Not really training as an ensemble, except for the test phase, when we try to see our stats as an ensemble
    '''
    use_random_ensemble=True
    precached_pkl = "simple_ensemble_train_data_40_segs_max_length_4.pkl"
    precached_test_pkl = "simple_ensemble_test_data_40_segs_max_length_4.pkl"
    max_num_samples=40 #number of samples of eeg data segments per eeg.edf file



@ex.named_config
def simple_ensemble():
    '''
    Not really training as an ensemble, except for the test phase, when we try to see our stats as an ensemble
    '''
    use_random_ensemble=True
    precached_pkl = "simple_ensemble_train_data_max_length_4.pkl"
    precached_test_pkl = "simple_ensemble_test_data_max_length_4.pkl"
    max_num_samples=40 #number of samples of eeg data segments per eeg.edf file

@ex.named_config
def combined_simple_ensemble():
    use_combined=True
    use_random_ensemble=True
    train_split="combined"
    test_split="combined"
    precached_pkl = "combined_simple_ensemble_train_data.pkl"
    precached_test_pkl = "combined_simple_ensemble_test_data.pkl"
    max_num_samples=40 #number of samples of eeg data segments per eeg.edf file



@ex.named_config
def combined():
    use_combined=True
    precached_pkl = "combined_train_data.pkl"
    precached_test_pkl = "combined_test_data.pkl"

@ex.named_config
def run_on_training_loss():
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
    start_offset_seconds = 0 #matters if we aren't doing random ensemble sampling
    dropout = 0.25
    use_early_stopping = True
    patience = 50
    model_name = randomString() + ".h5"
    precached_pkl = "train_data.pkl"
    precached_test_pkl = "test_data.pkl"
    num_epochs = 1000
    lr = 0.00005
    validation_size = 0.2
    test_size=0.2
    use_cached_pkl = True
    use_vp = True
    normalize_inputs = False
    #for custom architectures
    num_conv_spatial_layers=1
    num_conv_temporal_layers=1
    conv_spatial_filter=(3,3)
    num_spatial_filter=100
    conv_temporal_filter=(2,5)
    num_temporal_filter=300
    max_pool_size=(2,2)
    max_pool_stride=(1,2)
    use_random_ensemble = False
    max_num_samples=10 #number of samples of eeg data segments per eeg.edf file
    use_combined=False
    combined_split = "combined"
    early_stopping_on = "val_loss"
    test_train_split_pkl_path = "train_test_split_info.pkl"
    regenerate_data = False #if use_cached_pkl is false and this is true, just generates pickle files, doesn't make models or anything

#https://pynative.com/python-generate-random-string/
def randomString(stringLength=16):
    """Generate a random string of fixed length """
    letters = string.ascii_uppercase
    return ''.join(random.choice(letters) for i in range(stringLength))

@ex.capture
def get_model_checkpoint(model_name, monitor='val_loss'):
    return ModelCheckpoint(model_name, monitor=monitor, save_best_only=True, verbose=1)

@ex.capture
def get_early_stopping(patience, early_stopping_on):
    return EarlyStopping(patience=patience, verbose=1)
@ex.capture
def get_cb_list(use_early_stopping, model_name):
    if use_early_stopping:
        return [get_early_stopping(), get_model_checkpoint(), get_model_checkpoint("bin_acc_"+model_name, "val_binary_accuracy")]
    else:
        return [get_model_checkpoint(), get_model_checkpoint("bin_acc_"+model_name, "val_binary_accuracy")]

@ex.capture
def get_base_dataset(split, ref, n_process, num_files, use_random_ensemble, labels, max_num_samples, max_length, edfTokenPaths, start_offset_seconds):
    if not use_random_ensemble:
        edfData = read.EdfDataset(
            split,
            ref,
            n_process=n_process,
            max_length=max_length * pd.Timedelta(seconds=constants.COMMON_DELTA),
            use_numpy=True,
            start_offset=pd.Timedelta(seconds=start_offset_seconds)
            )
        edfData.edf_tokens = edfTokenPaths[:num_files]
        assert len(edfData) == len(labels)
        return edfData
    else:
        edfData = read.EdfDatasetEnsembler(
            split,
            ref,
            n_process=n_process,
            max_length=max_length * pd.Timedelta(seconds=constants.COMMON_DELTA),
            use_numpy=True,
            edf_tokens=edfTokenPaths[:num_files],
            labels=labels[:num_files],
            max_num_samples=max_num_samples,
            )

        # if split == "train":
        #     global trainEdfEnsembler
        #     trainEdfEnsembler = edfData
        # else:
        #     global testEdfEnsembler
        #     testEdfEnsembler = edfData
        edfData.verbosity = 50
        return edfData

cached_test_train_split_info = None
@ex.capture
def get_test_train_split_from_combined(combined_split, ref, test_size, use_cached_pkl, test_train_split_pkl_path):
    global cached_test_train_split_info
    if use_cached_pkl and path.exists(test_train_split_pkl_path):
        cached_test_train_split_info = pkl.load(open(test_train_split_pkl_path, 'rb'))
        ex.add_resource(test_train_split_pkl_path)
    elif cached_test_train_split_info is None:
        edfTokens, genders = cta.demux_to_tokens(cta.getGenderAndFileNames(combined_split, ref, convert_gender_to_num=True))
        cached_test_train_split_info = cta.train_test_split(edfTokens, genders, test_size=test_size)
        pkl.dump(cached_test_train_split_info, open(test_train_split_pkl_path, 'wb'))
        ex.add_artifact(test_train_split_pkl_path)
    return cached_test_train_split_info


@ex.capture
def get_data(split, ref, n_process, num_files, max_length, precached_pkl, use_cached_pkl, use_combined=False, train_split="", test_split="",  is_test=False):
    if use_combined:  #use the previous test train split, since we are sharing a split instead of enforcing it with a different directory
        edfTokensTrain, edfTokensTest, gendersTrain, gendersTest = get_test_train_split_from_combined()
        if split==train_split and not is_test:
            edfTokenPaths = edfTokensTrain[:num_files]
            genders = np.array(gendersTrain[:num_files])
            assert len(edfTokenPaths) == len(genders)
        elif split == test_split or is_test:
            edfTokenPaths = edfTokensTest[:num_files]
            genders = np.array(gendersTest[:num_files])
    else:
        genderDict = cta.getGenderAndFileNames(split, ref, convert_gender_to_num=True)
        edfTokenPaths, genders = cta.demux_to_tokens(genderDict)
    if path.exists(precached_pkl) and use_cached_pkl:
        edfData = pkl.load(open(precached_pkl, 'rb'))
    else:
        edfData = get_base_dataset(split, labels=genders, edfTokenPaths=edfTokenPaths)
        edfData = edfData[:]
        pkl.dump(edfData, open(precached_pkl, 'wb')) #don't add these as artifacts or resources or else mongodb will try to store giant file copies of these
    return edfData, genders

@ex.capture
def get_data_generator(split, batch_size, num_files, max_length, use_random_ensemble):
    """Based on a really naive, dumb mapping of eeg electrodes into 2d space

    Parameters
    ----------
    split : type
        Description of parameter `split`.
    ref : type
        Description of parameter `ref`.
    n_process : type
        Description of parameter `n_process`.

    Returns
    -------
    type
        Description of returned object.

    """
    edfData, genders = get_data(split)
    return EdfDataGenerator(
        edfData,
        precache=True,
        time_first=False,
        n_classes=2,
        labels=np.array(genders) if not use_random_ensemble else None, #properly duplicated genders inside edfData if using use_random_ensemble
        batch_size=batch_size,
        max_length=max_length)

@ex.capture
def get_model(dropout, max_length,lr, use_vp, num_spatial_filter):
    if use_vp:
        model = vp_conv2d(dropout=dropout, input_shape=(21, max_length, 1), filter_size=num_spatial_filter)
        adam = optimizers.Adam(lr=lr)
        model.compile(adam, loss="categorical_crossentropy", metrics=["binary_accuracy"])
        return model
    else:
        return get_custom_model()

@ex.capture
def get_custom_model(
    dropout,
    max_length,
    lr,
    num_conv_spatial_layers=1,
    num_conv_temporal_layers=1,
    conv_spatial_filter=(3,3),
    num_spatial_filter=100,
    conv_temporal_filter=(2,5),
    num_temporal_filter=300,
    max_pool_size=(2,2),
    max_pool_stride=(1,2)
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
        max_pool_stride=max_pool_stride
        )
    adam = optimizers.Adam(lr=lr)
    model.compile(adam, loss="categorical_crossentropy", metrics=["binary_accuracy"])
    return model


@ex.capture
def get_test_data(test_split, max_length, precached_test_pkl, use_cached_pkl):
    testData, testGender = get_data(test_split, precached_pkl=precached_test_pkl, is_test=True)
    testData = np.stack([datum[0] for datum in testData])
    testData = testData[:, 0:max_length]
    testData=testData.reshape(*testData.shape, 1).transpose(0,2,1,3)
    return testData, testGender

@ex.main
def main(train_split, test_split, num_epochs, lr, n_process, validation_size, max_length, use_random_ensemble, ref, num_files, use_combined, regenerate_data, model_name):
    trainValidationDataGenerator = get_data_generator(train_split)
    trainDataGenerator, validationDataGenerator = trainValidationDataGenerator.create_validation_train_split(validation_size=validation_size)
    model = get_model()
    print(model.summary())

    print("x batch shape", len(trainDataGenerator))
    if not regenerate_data:
        history = model.fit_generator(trainDataGenerator, epochs=num_epochs, callbacks=get_cb_list(), validation_data=validationDataGenerator)


    testData, testGender = get_test_data()
    if use_random_ensemble: #regenerate the dictionary structure to get correct labeling back and access to mapping back to original edfToken space
        if not use_combined:
            edfTokenPaths, testGender = cta.demux_to_tokens(cta.getGenderAndFileNames(test_split, ref))
            testEdfEnsembler = get_base_dataset(test_split, labels=testGender, edfTokenPaths=edfTokenPaths)
        else:
            trainEdfTokens, edfTokenPaths, trainGenders, _testGendersCopy = get_test_train_split_from_combined()
            testEdfEnsembler = get_base_dataset(test_split, labels=_testGendersCopy, edfTokenPaths=edfTokenPaths)

        testGender = testEdfEnsembler.getEnsembledLabels()
        assert len(testData) == len(testGender)

    if regenerate_data:
        return
    model = keras.models.load_model(model_name)
    bin_acc_model = keras.models.load_model("bin_acc_" + model_name)

    y_pred = model.predict(testData)
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
    bin_acc_accuracy = accuracy_score(testGender, y_pred_bin_acc.argmax(axis=1))


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
    if use_random_ensemble:
        label, average_pred = testEdfEnsembler.getEnsemblePrediction(y_pred.argmax(axis=1))
        pred = np.round(average_pred)
        toReturn["ensemble_score"] = {}
        toReturn["ensemble_score"]["auc"] = roc_auc_score(label, pred)
        toReturn["ensemble_score"]["acc"] = accuracy_score(label, pred)
        toReturn["ensemble_score"]["f1_score"] = f1_score(label, pred)
        toReturn["ensemble_score"]["discordance"] = np.abs(pred - average_pred).mean()
    return toReturn
if __name__ == "__main__":
    ex.run_commandline()
