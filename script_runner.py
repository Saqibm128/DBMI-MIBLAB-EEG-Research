from multiprocessing import Process, Manager
import multiprocessing as mp
from initial_clustering import ex
import sys

q = Manager().Queue()

def runExperiment(argsQueue):
    """Runs an experiment using args from a queue until None signal at end.
    Parameters
    ----------
    argsQueue : queue
        Holds tuples, with first value a list of named_configs (str)
        and second value a dictionary of config_updates.
    Returns
    -------
    None
    """
    for experArg in iter(argsQueue.get, None):
        try:
            ex.run(config_updates=experArg[1], named_configs=experArg[0])
        except Exception as e:
            print(e)
    return

m = Manager()
argsQueue = m.Queue()
num_processes = mp.cpu_count()

for num_k_means in range(1, 40, 2):
    for num_pca_comp in range(1, 40, 2):
        argsQueue.put((["attach_mongo"], {'num_pca_comps':num_pca_comp, 'num_k_means':num_k_means, 'precached_pkl': sys.argv[1]}))

processes = [Process(target=runExperiment, args=(argsQueue,)) for i in range(num_processes)]
[argsQueue.put(None) for i in range(num_processes)]
[process.start() for process in processes]
[process.join() for process in processes]
