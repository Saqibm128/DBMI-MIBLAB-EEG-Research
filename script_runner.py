from multiprocessing import Process, Manager
import multiprocessing as mp
from initial_clustering import ex
import sys
import time
import random
import argparse

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
            # adds wiggle room for mongodb observer
            time.sleep(random.random() * 5)
            ex.run(config_updates=experArg[1], named_configs=experArg[0])
        except Exception as e:
            print(e)
    return


m = Manager()
argsQueue = m.Queue()
num_processes = mp.cpu_count()
# num_processes = 1

parser = argparse.ArgumentParser()
parser.add_argument("path", type=str)
parser.add_argument("--target", type=str, default='initial_clustering')
parser.add_argument("--num_process", type=int, default=None)
parser.add_argument("--dim_red", type=str, default='pca')
args = parser.parse_args()

if args.target == 'initial_clustering':
    for num_k_means in range(1, 40, 2):
        for num_pca_comp in range(1, 40, 2):
            argsQueue.put(([],
                           {'num_comps': num_pca_comp,
                            'num_clusters': num_k_means,
                            'precached_pkl': args.path,
                            'dim_red': args.dim_red}))

    if args.num_process is not None:
        num_processes = args.num_process
    print("Num Processes: {}".format(num_processes))
    processes = [Process(target=runExperiment, args=(argsQueue,))
                 for i in range(num_processes)]
    [argsQueue.put(None) for i in range(num_processes)]
    [process.start() for process in processes]
    [process.join() for process in processes]
