from copy import deepcopy
import numpy as np
from scipy.sparse import lil_matrix
from scipy import sparse
from scipy.sparse import csr_matrix, vstack

import logging

from app_globals import *  # get_command_args, Opts, configure_logger
from alad_support import *
from r_support import matrix, cbind, Timer
import numbers
from forest_aad_loss import *
from random_split_trees import *
from gp_support import *
from optimization import *

import pickle as cPickle
import gzip

from joblib import Parallel, delayed


class RegionData(object):
    def __init__(self, region, path_length, node_id, score, node_samples, log_frac_vol=0.0):
        self.region = region
        self.path_length = path_length
        self.node_id = node_id
        self.score = score
        self.node_samples = node_samples
        self.log_frac_vol = log_frac_vol
        # print "Region's Path Length="+str(path_length)+"---"+str(node_samples)


def is_in_region(x, region):
    d = len(x)
    for i in range(d):
        if not region[i][0] <= x[i] <= region[i][1]:
            return False
    return True


def transform_features(x, all_regions, d):
    """ Inefficient method for looking up region membership.

    Note: This method is only for DEBUG. For a faster
    implementation, see below.
    @see: AadIsolationForest.transform_to_region_features

    :param x:
    :param all_regions:
    :param d:
    :return:
    """
    # translate x's to new coordinates
    x_new = np.zeros(shape=(x.shape[0], len(d)), dtype=np.float64)
    for i in range(x.shape[0]):
        for j, region in enumerate(all_regions):
            if is_in_region(x[i, :], region[0]):
                x_new[i, j] = d[j]
    return x_new


class AadForest(StreamingSupport):
    def __init__(self, n_estimators=10, max_samples=100, max_depth=10,
                 score_type=LEAF_INV_SAMPLE_SCORING,
                 ensemble_score=ENSEMBLE_SCORE_LINEAR,
                 random_state=None,
                 add_leaf_nodes_only=False,
                 detector_type=AAD_IFOREST, n_jobs=1, model=None):
        if random_state is None:
            self.random_state = np.random.RandomState(42)
        else:
            self.random_state = random_state

        self.detector_type = detector_type

        self.n_estimators = n_estimators
        self.max_samples = max_samples

        self.score_type = score_type
        if not (self.score_type == IFOR_SCORE_TYPE_INV_PATH_LEN or
                        self.score_type == IFOR_SCORE_TYPE_INV_PATH_LEN_EXP or
                        self.score_type == IFOR_SCORE_TYPE_CONST or
                        self.score_type == IFOR_SCORE_TYPE_NEG_PATH_LEN or
                        self.score_type == HST_SCORE_TYPE or
                        self.score_type == RSF_SCORE_TYPE or
                        self.score_type == RSF_LOG_SCORE_TYPE or
                        self.score_type == ORIG_TREE_SCORE_TYPE or
                        self.score_type == LEAF_INV_SAMPLE_SCORING):
            raise NotImplementedError("score_type %d not implemented!" % self.score_type)

        self.ensemble_score = ensemble_score
        self.add_leaf_nodes_only = add_leaf_nodes_only
        self.clf = model
        if detector_type == AAD_IFOREST:
            # setting clf to be learned model from my code
            self.clf = model  # IForest(n_estimators=n_estimators, max_samples=max_samples,
            # n_jobs=n_jobs, random_state=self.random_state)
        elif detector_type == AAD_HSTREES:
            self.clf = HSTrees(n_estimators=n_estimators, max_depth=max_depth,
                               n_jobs=n_jobs, random_state=self.random_state)
        elif detector_type == AAD_RSFOREST:
            self.clf = RSForest(n_estimators=n_estimators, max_depth=max_depth,
                                n_jobs=n_jobs, random_state=self.random_state)
        else:
            raise ValueError("Incorrect detector type: %d. Only tree-based detectors (%d|%d|%d) supported." %
                             (detector_type, AAD_IFOREST, AAD_HSTREES, AAD_RSFOREST))

        # store all regions grouped by tree
        self.regions_in_forest = None

        # store all regions in a flattened list (ungrouped)
        self.all_regions = None

        # store maps of node index to region index for all trees
        self.all_node_regions = None

        # scores for each region
        self.d = None

        # samples for each region
        # self.node_samples = None

        # fraction of instances in each region
        # self.frac_insts = None

        # node weights learned through weak-supervision
        self.w = None

        # quick lookup of the uniform weight vector.
        # IMPORTANT: Treat this as readonly once set in fit()
        self.w_unif_prior = None

    def fit_bkp(self, x):
        tm = Timer()

        tm.start()
        ### commented for now to make it compatible with my code.
        ## I am passing perviously trained models
        # self.clf.fit(x)
        # print len(clf.estimators_)
        # print type(clf.estimators_[0].tree_)

        logger.debug(tm.message("created original forest"))

        if self.score_type == ORIG_TREE_SCORE_TYPE:
            # no need to extract regions in this case
            return

        tm.start()
        self.regions_in_forest = []
        self.all_regions = []
        self.all_node_regions = []
        region_id = 0
        for i in range(len(self.clf.estimators_)):
            regions = self.extract_leaf_regions_from_tree(self.clf.estimators_[i],
                                                          self.add_leaf_nodes_only)
            self.regions_in_forest.append(regions)
            self.all_regions.extend(regions)
            node_regions = {}
            for region in regions:
                node_regions[region.node_id] = region_id
                region_id += 1  # this will monotonously increase across trees
            self.all_node_regions.append(node_regions)
            # print "%d, #nodes: %d" % (i, len(regions))
        self.d, _, _ = self.get_region_scores(self.all_regions)
        self.w = self.get_uniform_weights()
        self.w_unif_prior = self.get_uniform_weights()
        logger.debug(tm.message("created forest regions"))

    def fit(self, x, multi=False):
        tm = Timer()

        tm.start()
        ### commented for now to make it compatible with my code.
        ## I am passing perviously trained models
        # self.clf.fit(x)
        # print len(clf.estimators_)
        # print type(clf.estimators_[0].tree_)

        logger.debug(tm.message("created original forest"))

        if self.score_type == ORIG_TREE_SCORE_TYPE:
            # no need to extract regions in this case
            return

        tm.start()
        self.regions_in_forest = []
        self.all_regions = []
        self.all_node_regions = []
        region_id = 0
        if multi:
            argument_instances = [(i, self.clf.estimators_[i], self.add_leaf_nodes_only) for i in
                                  range(len(self.clf.estimators_))]
            lst_regions = Parallel(n_jobs=-2, verbose=1)(map(delayed(self.worker_extract_leaf_regions_from_tree),
                                                             argument_instances))
            lst_regions.sort(key=lambda x: x[0])
            lst_regions = [x[1] for x in lst_regions]

            self.regions_in_forest = lst_regions
            self.all_regions = [item for sublist in lst_regions for item in sublist]

            for regions in lst_regions:
                node_regions = {}
                for region in regions:
                    node_regions[region.node_id] = region_id
                    region_id += 1  # this will monotonously increase across trees
                self.all_node_regions.append(node_regions)

        else:
            for i in range(len(self.clf.estimators_)):
                regions = self.extract_leaf_regions_from_tree(self.clf.estimators_[i],
                                                              self.add_leaf_nodes_only)
                self.regions_in_forest.append(regions)
                self.all_regions.extend(regions)
                node_regions = {}
                for region in regions:
                    node_regions[region.node_id] = region_id
                    region_id += 1  # this will monotonously increase across trees
                self.all_node_regions.append(node_regions)
                # print "%d, #nodes: %d" % (i, len(regions))
        self.d, _, _ = self.get_region_scores(self.all_regions)
        self.w = self.get_uniform_weights()
        self.w_unif_prior = self.get_uniform_weights()
        logger.debug(tm.message("created forest regions"))

    def worker_extract_leaf_regions_from_tree(self, arg):
        tree_number, tree, add_leaf_nodes_only = arg
        return tree_number, self.extract_leaf_regions_from_tree(tree, add_leaf_nodes_only)

    def extract_leaf_regions_from_tree(self, tree, add_leaf_nodes_only=False):
        """Extracts leaf regions from decision tree.

        Returns each decision path as array of strings representing
        node comparisons.

        Args:
            tree: sklearn.tree
                A trained decision tree.
            add_leaf_nodes_only: bool
                whether to extract only leaf node regions or include 
                internal node regions as well

        Returns: list of
        """

        add_intermediate_nodes = not add_leaf_nodes_only

        left = tree.tree_.children_left
        right = tree.tree_.children_right
        features = tree.tree_.feature
        threshold = tree.tree_.threshold
        node_samples = tree.tree_.n_node_samples
        log_frac_vol = None
        if isinstance(tree.tree_, ArrTree):
            log_frac_vol = tree.tree_.acc_log_v

        # value = tree.tree_.value

        full_region = {}
        for fidx in range(tree.tree_.n_features):
            full_region[fidx] = (-np.inf, np.inf)

        regions = []

        def recurse(left, right, features, threshold, node, region, path_length=0):

            if left[node] == -1 and right[node] == -1:
                # we have reached a leaf node
                # print region
                regions.append(RegionData(deepcopy(region), path_length, node,
                                          self._average_path_length(node_samples[node]),
                                          node_samples[node],
                                          log_frac_vol=0. if log_frac_vol is None else log_frac_vol[node]))
                return
            elif left[node] == -1 or right[node] == -1:
                print("dubious node...")

            feature = features[node]

            if add_intermediate_nodes and node != 0:
                regions.append(RegionData(deepcopy(region), path_length, node,
                                          self._average_path_length(node_samples[node]),
                                          node_samples[node],
                                          log_frac_vol=0. if log_frac_vol is None else log_frac_vol[node]))

            if left[node] != -1:
                # make a copy to send down the next node so that
                # the previous value is unchanged when we backtrack.
                new_region = deepcopy(region)
                new_region[feature] = (new_region[feature][0], min(new_region[feature][1], threshold[node]))
                recurse(left, right, features, threshold, left[node], new_region, path_length + 1)

            if right[node] != -1:
                # make a copy for the reason mentioned earlier.
                new_region = deepcopy(region)
                new_region[feature] = (max(new_region[feature][0], threshold[node]), new_region[feature][1])
                recurse(left, right, features, threshold, right[node], new_region, path_length + 1)

        recurse(left, right, features, threshold, 0, full_region)
        return regions

    def _average_path_length(self, n_samples_leaf):
        """ The average path length in a n_samples iTree, which is equal to
        the average path length of an unsuccessful BST search since the
        latter has the same structure as an isolation tree.
        Parameters
        ----------
        n_samples_leaf : array-like of shape (n_samples, n_estimators), or int.
            The number of training samples in each test sample leaf, for
            each estimators.

        Returns
        -------
        average_path_length : array, same shape as n_samples_leaf

        """
        if n_samples_leaf <= 1:
            return 1.
        else:
            return 2. * (np.log(n_samples_leaf) + 0.5772156649) - 2. * (
                n_samples_leaf - 1.) / n_samples_leaf

    def decision_path_full(self, x, tree):
        """Returns the node ids of all nodes from root to leaf for each sample (row) in x
        
        Args:
            x: numpy.ndarray
            tree: fitted decision tree
        
        Returns: list of length x.shape[0]
            list of lists
        """

        left = tree.tree_.children_left
        right = tree.tree_.children_right
        features = tree.tree_.feature
        threshold = tree.tree_.threshold

        def path_recurse(x, left, right, features, threshold, node, path_nodes):
            """Returns the node ids of all nodes that x passes through from root to leaf
            
            Args:
                x: numpy.array
                    a single instance
                path_nodes: list
            """

            if left[node] == -1 and right[node] == -1:
                # reached a leaf
                return
            else:
                feature = features[node]
                if x[feature] <= threshold[node]:
                    next_node = left[node]
                else:
                    next_node = right[node]
                path_nodes.append(next_node)
                path_recurse(x, left, right, features, threshold, next_node, path_nodes)

        n = x.shape[0]
        all_path_nodes = []
        for i in range(n):
            path_nodes = []
            path_recurse(x[i, :], left, right, features, threshold, 0, path_nodes)
            all_path_nodes.append(path_nodes)
        return all_path_nodes

    def decision_path_leaf(self, x, tree):
        n = x.shape[0]
        all_path_nodes = []

        # get all leaf nodes
        node_idxs = tree.apply(x)
        # logger.debug("node_idxs:\n%s" % str(node_idxs))

        for j in range(n):
            all_path_nodes.append([node_idxs[j]])

        return all_path_nodes

    def get_decision_path(self, x, tree):
        if self.add_leaf_nodes_only:
            return self.decision_path_leaf(x, tree)
        else:
            return self.decision_path_full(x, tree)

    def get_region_scores(self, all_regions):
        """Larger values mean more anomalous"""
        d = np.zeros(len(all_regions))
        node_samples = np.zeros(len(all_regions))
        frac_insts = np.zeros(len(all_regions))
        for i, region in enumerate(all_regions):
            node_samples[i] = region.node_samples
            frac_insts[i] = region.node_samples * 1.0 / self.max_samples
            if self.score_type == IFOR_SCORE_TYPE_INV_PATH_LEN:
                d[i] = 1. / region.path_length
            elif self.score_type == IFOR_SCORE_TYPE_INV_PATH_LEN_EXP:
                d[i] = 2 ** -region.path_length  # used this to run the first batch
            elif self.score_type == IFOR_SCORE_TYPE_CONST:
                d[i] = -1
            elif self.score_type == IFOR_SCORE_TYPE_NEG_PATH_LEN:
                d[i] = -region.path_length
            elif self.score_type == HST_SCORE_TYPE:
                # d[i] = -region.node_samples * (2. ** region.path_length)
                # d[i] = -region.node_samples * region.path_length
                d[i] = -np.log(region.node_samples + 1) + region.path_length
            elif self.score_type == RSF_SCORE_TYPE:
                d[i] = -region.node_samples * np.exp(region.log_frac_vol)
            elif self.score_type == RSF_LOG_SCORE_TYPE:
                d[i] = -np.log(region.node_samples + 1) - region.log_frac_vol
            elif self.score_type == LEAF_INV_SAMPLE_SCORING:
                d[i] = 1. / (region.path_length + self._average_path_length(region.node_samples))
            else:
                # if self.score_type == IFOR_SCORE_TYPE_NORM:
                raise NotImplementedError("score_type %d not implemented!" % self.score_type)
                # d[i] = frac_insts[i]  # RPAD-ish
                # depth = region.path_length - 1
                # node_samples_avg_path_length = region.score
                # d[i] = (
                #            depth + node_samples_avg_path_length
                #        ) / (self.n_estimators * self._average_path_length(self.clf._max_samples))
        return d, node_samples, frac_insts

    def get_score(self, x, w=None):
        """Higher score means more anomalous"""
        # if self.score_type == IFOR_SCORE_TYPE_INV_PATH_LEN or \
        #                self.score_type == IFOR_SCORE_TYPE_INV_PATH_LEN_EXP or \
        #                self.score_type == IFOR_SCORE_TYPE_CONST or \
        #                self.score_type == IFOR_SCORE_TYPE_NEG_PATH_LEN or \
        #                self.score_type == HST_SCORE_TYPE:
        if w is None:
            w = self.w
        if w is None:
            raise ValueError("weights not initialized")
        if self.ensemble_score == ENSEMBLE_SCORE_LINEAR:
            return x.dot(w)
        elif self.ensemble_score == ENSEMBLE_SCORE_EXPONENTIAL:
            return np.exp(x.dot(w))
        else:
            raise NotImplementedError("score_type %d not implemented!" % self.score_type)

    def decision_function(self, x):
        """Returns the decision function for the original underlying classifier"""
        return self.clf.decision_function(x)

    def supports_streaming(self):
        return self.clf.supports_streaming()

    def add_samples(self, X, current=False):
        """Incrementally updates the stream buffer node counts"""
        if not self.supports_streaming():
            raise ValueError("Detector does not support incremental update")
        if current:
            raise ValueError("Only current=False supported")
        self.clf.add_samples(X, current=current)

    def update_region_scores(self):
        for i, estimator in enumerate(self.clf.estimators_):
            tree = estimator.tree_
            node_regions = self.all_node_regions[i]
            for node_id in node_regions:
                region_id = node_regions[node_id]
                self.all_regions[region_id].node_samples = tree.n_node_samples[node_id]
        self.d, _, _ = self.get_region_scores(self.all_regions)

    def update_model_from_stream_buffer(self):
        self.clf.update_model_from_stream_buffer()
        # for i, estimator in enumerate(self.clf.estimators_):
        #    estimator.tree.tree_.update_model_from_stream_buffer()
        self.update_region_scores()

    def get_region_score_for_instance_transform(self, region_id, norm_factor=1.0):
        if (self.score_type == IFOR_SCORE_TYPE_CONST or
                    self.score_type == HST_SCORE_TYPE or
                    self.score_type == RSF_SCORE_TYPE or
                    self.score_type == RSF_LOG_SCORE_TYPE):
            return self.d[region_id]
        elif self.score_type == ORIG_TREE_SCORE_TYPE:
            raise ValueError(
                "Score type %d not supported for method get_region_score_for_instance_transform()" % self.score_type)
        else:
            return self.d[region_id] / norm_factor

    def transform_to_region_features(self, x, dense=True, multi=False):
        """ Transforms matrix x to features from isolation forest

        :param x: np.ndarray
            Input data in original feature space
        :param dense: bool
            Whether to return a dense matrix or sparse. The number
            of features in isolation forest correspond to the nodes
            which might be thousands in number. However, each instance
            (row) in x will have only as many non-zero values as the
            number of trees -- which is *much* smaller than the number
            of nodes.
        :return:
        """
        if dense:
            return self.transform_to_region_features_dense(x)
        else:
            return self.transform_to_region_features_sparse(x, multi)

    def transform_to_region_features_dense(self, x):
        # return transform_features(x, self.all_regions, self.d)
        x_new = np.zeros(shape=(x.shape[0], len(self.d)), dtype=float)
        self._transform_to_region_features_with_lookup(x, x_new)
        return x_new

    def transform_to_region_features_sparse_bkp(self, x):
        """ Transforms from original feature space to IF node space
        
        The conversion to sparse vectors seems to take a lot of intermediate
        memory in python. This is why we are converting the vectors in smaller
        batches. The transformation is a one-time task, hence not a concern in 
        most cases.
        
        :param x: 
        :return: 
        """
        # logger.debug("transforming to IF feature space...")
        n = x.shape[0]
        m = len(self.d)
        batch_size = 10000
        start_batch = 0
        end_batch = min(start_batch + batch_size, n)
        x_new = csr_matrix((0, m), dtype=float)
        while start_batch < end_batch:
            starttime = timer()
            x_tmp = matrix(x[start_batch:end_batch, :], ncol=x.shape[1])
            x_tmp_new = lil_matrix((end_batch - start_batch, m), dtype=x_new.dtype)
            for i, tree in enumerate(self.clf.estimators_):
                n_tmp = x_tmp.shape[0]
                node_regions = self.all_node_regions[i]
                tree_paths = self.get_decision_path(x_tmp, tree)
                for j in range(n_tmp):
                    k = len(tree_paths[j])
                    for node_idx in tree_paths[j]:
                        region_id = node_regions[node_idx]
                        x_tmp_new[j, region_id] = self.get_region_score_for_instance_transform(region_id, k)
            if n >= 100000:
                endtime = timer()
                tdiff = difftime(endtime, starttime, units="secs")
                logger.debug("processed %d/%d (%f); batch %d in %f sec(s)" %
                             (end_batch + 1, n, (end_batch + 1) * 1. / n, batch_size, tdiff))
            x_new = vstack([x_new, x_tmp_new.tocsr()])
            start_batch = end_batch
            end_batch = min(start_batch + batch_size, n)
        return x_new

    def transform_to_region_features_sparse(self, x, multi=False):
        """ Transforms from original feature space to IF node space

        The conversion to sparse vectors seems to take a lot of intermediate
        memory in python. This is why we are converting the vectors in smaller
        batches. The transformation is a one-time task, hence not a concern in
        most cases.

        :param x:
        :return:
        """
        # logger.debug("transforming to IF feature space...")
        n = x.shape[0]
        m = len(self.d)
        batch_size = 10000
        start_batch = 0
        end_batch = min(start_batch + batch_size, n)
        x_new = csr_matrix((0, m), dtype=float)
        while start_batch < end_batch:
            starttime = timer()
            x_tmp = matrix(x[start_batch:end_batch, :], ncol=x.shape[1])
            x_tmp_new = lil_matrix((end_batch - start_batch, m), dtype=x_new.dtype)

            if multi:
                n_tmp = x_tmp.shape[0]
                argument_instances = [(x_tmp, n_tmp, i, self.clf.estimators_[i])
                                      for i in range(len(self.clf.estimators_))]
                lst_results = Parallel(n_jobs=-2, verbose=1)(map(delayed(self.worker_transform),
                                                                 argument_instances))
                for result in lst_results:
                    for _tup in result:
                        x_tmp_new[_tup[0], _tup[1]] = _tup[2]

            else:
                for i, tree in enumerate(self.clf.estimators_):
                    n_tmp = x_tmp.shape[0]
                    node_regions = self.all_node_regions[i]
                    tree_paths = self.get_decision_path(x_tmp, tree)

                    for j in range(n_tmp):
                        k = len(tree_paths[j])
                        for node_idx in tree_paths[j]:
                            region_id = node_regions[node_idx]
                            x_tmp_new[j, region_id] = self.get_region_score_for_instance_transform(region_id, k)

            if n >= 100000:
                endtime = timer()
                tdiff = difftime(endtime, starttime, units="secs")
                logger.debug("processed %d/%d (%f); batch %d in %f sec(s)" %
                             (end_batch + 1, n, (end_batch + 1) * 1. / n, batch_size, tdiff))
            x_new = vstack([x_new, x_tmp_new.tocsr()])
            start_batch = end_batch
            end_batch = min(start_batch + batch_size, n)
        return x_new

    def worker_transform(self, args):
        x_tmp, n_tmp, i, tree = args
        node_regions = self.all_node_regions[i]
        tree_paths = self.get_decision_path(x_tmp, tree)

        result = []
        for j in range(n_tmp):
            k = len(tree_paths[j])
            for node_idx in tree_paths[j]:
                region_id = node_regions[node_idx]
                result.append((j, region_id, self.get_region_score_for_instance_transform(region_id, k)))

        return result

    def _transform_to_region_features_with_lookup(self, x, x_new):
        """ Transforms from original feature space to IF node space

        NOTE: This has been deprecated. Will be removed in future.

        Performs the conversion tree-by-tree. Even with batching by trees,
        this requires a lot of intermediate memory. Hence we do not use this method...

        :param x:
        :param x_new:
        :return:
        """
        starttime = timer()
        n = x_new.shape[0]
        for i, tree in enumerate(self.clf.estimators_):
            node_regions = self.all_node_regions[i]
            for j in range(n):
                tree_paths = self.get_decision_path(matrix(x[j, :], nrow=1), tree)
                k = len(tree_paths[0])
                for node_idx in tree_paths[0]:
                    region_id = node_regions[node_idx]
                    x_new[j, region_id] = self.get_region_score_for_instance_transform(region_id, k)
                if j >= 100000:
                    if j % 20000 == 0:
                        endtime = timer()
                        tdiff = difftime(endtime, starttime, units="secs")
                        logger.debug("processed %d/%d trees, %d/%d (%f) in %f sec(s)" %
                                     (i, len(self.clf.estimators_), j + 1, n, (j + 1) * 1. / n, tdiff))

    def get_tau_ranked_instance(self, x, w, tau_rank):
        s = self.get_score(x, w)
        ps = order(s, decreasing=True)[tau_rank]
        return matrix(x[ps, :], nrow=1)

    def get_aatp_quantile(self, x, w, topK):
        # IMPORTANT: qval will be computed using the linear dot product
        # s = self.get_score(x, w)
        s = x.dot(w)
        return quantile(s, (1.0 - (topK * 1.0 / float(nrow(x)))) * 100.0)

    def get_truncated_constraint_set(self, w, x, y, hf,
                                     max_anomalies_in_constraint_set=1000,
                                     max_nominals_in_constraint_set=1000):
        hf_tmp = np.array(hf)
        yf = y[hf_tmp]
        ha_pos = np.where(yf == 1)[0]
        hn_pos = np.where(yf == 0)[0]

        if len(ha_pos) > 0:
            ha = hf_tmp[ha_pos]
        else:
            ha = np.array([], dtype=int)

        if len(hn_pos) > 0:
            hn = hf_tmp[hn_pos]
        else:
            hn = np.array([], dtype=int)

        if len(ha) > max_anomalies_in_constraint_set or \
                        len(hn) > max_nominals_in_constraint_set:
            # logger.debug("len(ha) %d, len(hn) %d; random selection subset" % (len(ha), len(hn)))
            in_set_ha = np.zeros(len(ha), dtype=int)
            in_set_hn = np.zeros(len(hn), dtype=int)
            if len(ha) > max_anomalies_in_constraint_set:
                tmp = sample(range(len(ha)), max_anomalies_in_constraint_set)
                in_set_ha[tmp] = 1
            else:
                in_set_ha[:] = 1
            if len(hn) > max_nominals_in_constraint_set:
                tmp = sample(range(len(hn)), max_nominals_in_constraint_set)
                in_set_hn[tmp] = 1
            else:
                in_set_hn[:] = 1
            hf = append(ha, hn)
            in_set = append(in_set_ha, in_set_hn)
            # logger.debug(in_set)
        else:
            in_set = np.ones(len(hf), dtype=int)

        return hf, in_set

    def forest_aad_weight_update(self, w, x, y, hf, w_prior, opts, tau_rel=False, linear=True):
        n = x.shape[0]
        bt = get_budget_topK(n, opts)

        qval = self.get_aatp_quantile(x, w, bt.topK)

        hf, in_constr_set = self.get_truncated_constraint_set(w, x, y, hf,
                                                              max_anomalies_in_constraint_set=opts.max_anomalies_in_constraint_set,
                                                              max_nominals_in_constraint_set=opts.max_nominals_in_constraint_set)

        # logger.debug("Linear: %s, sigma2: %f, with_prior: %s" %
        #              (str(linear), opts.priorsigma2, str(opts.withprior)))

        x_tau = None
        if tau_rel:
            x_tau = self.get_tau_ranked_instance(x, w, bt.topK)
            # logger.debug("x_tau:")
            # logger.debug(to_dense_mat(x_tau))

        def if_f(w, x, y):
            if linear:
                return forest_aad_loss_linear(w, x, y, qval, in_constr_set=in_constr_set, x_tau=x_tau,
                                              Ca=opts.Ca, Cn=opts.Cn, Cx=opts.Cx,
                                              withprior=opts.withprior, w_prior=w_prior,
                                              sigma2=opts.priorsigma2)
            else:
                return forest_aad_loss_exp(w, x, y, qval, in_constr_set=in_constr_set, x_tau=x_tau,
                                           Ca=opts.Ca, Cn=opts.Cn, Cx=opts.Cx,
                                           withprior=opts.withprior, w_prior=w_prior,
                                           sigma2=opts.priorsigma2)

        def if_g(w, x, y):
            if linear:
                return forest_aad_loss_gradient_linear(w, x, y, qval, in_constr_set=in_constr_set, x_tau=x_tau,
                                                       Ca=opts.Ca, Cn=opts.Cn, Cx=opts.Cx,
                                                       withprior=opts.withprior, w_prior=w_prior,
                                                       sigma2=opts.priorsigma2)
            else:
                return forest_aad_loss_gradient_exp(w, x, y, qval, in_constr_set=in_constr_set, x_tau=x_tau,
                                                    Ca=opts.Ca, Cn=opts.Cn, Cx=opts.Cx,
                                                    withprior=opts.withprior, w_prior=w_prior,
                                                    sigma2=opts.priorsigma2)

        if False:
            w_new = sgd(w, x[hf, :], y[hf], if_f, if_g,
                        learning_rate=0.001, max_epochs=1000, eps=1e-5,
                        shuffle=True, rng=self.random_state)
        elif False:
            w_new = sgdMomentum(w, x[hf, :], y[hf], if_f, if_g,
                                learning_rate=0.001, max_epochs=1000,
                                shuffle=True, rng=self.random_state)
        elif True:
            # sgdRMSProp seems to run fastest and achieve performance close to best
            # NOTE: this was an observation on ANNThyroid_1v3 and toy2 datasets
            w_new = sgdRMSProp(w, x[hf, :], y[hf], if_f, if_g,
                               learning_rate=0.001, max_epochs=1000,
                               shuffle=True, rng=self.random_state)
        elif False:
            # sgdAdam seems to get best performance while a little slower than sgdRMSProp
            # NOTE: this was an observation on ANNThyroid_1v3 and toy2 datasets
            w_new = sgdAdam(w, x[hf, :], y[hf], if_f, if_g,
                            learning_rate=0.001, max_epochs=1000,
                            shuffle=True, rng=self.random_state)
        else:
            w_new = sgdRMSPropNestorov(w, x[hf, :], y[hf], if_f, if_g,
                                       learning_rate=0.001, max_epochs=1000,
                                       shuffle=True, rng=self.random_state)
        w_len = w_new.dot(w_new)
        # logger.debug("w_len: %f" % w_len)
        if np.isnan(w_len):
            # logger.debug("w_new:\n%s" % str(list(w_new)))
            raise ArithmeticError("weight vector contains nan")
        w_new = w_new / np.sqrt(w_len)
        return w_new

    def get_uniform_weights(self, m=None):
        if m is None:
            m = len(self.d)
        w_unif = np.ones(m, dtype=float)
        w_unif = w_unif / np.sqrt(w_unif.dot(w_unif))
        # logger.debug("w_prior:")
        # logger.debug(w_unif)
        return w_unif

    def order_by_score(self, x, w=None):
        anom_score = self.get_score(x, w)
        return order(anom_score, decreasing=True), anom_score

    def update_weights(self, x, y, ha, hn, opts, w=None):
        """Learns new weights for one feedback iteration

        Args:
            x: np.ndarray
                input data
            y: np.array(dtype=int)
                labels. Only the values at indexes in ha and hn are relevant. Rest may be np.nan.
            ha: np.array(dtype=int)
                indexes of labeled anomalies in x
            hn: indexes of labeled nominals in x
            opts: Opts
            w: np.array(dtype=float)
                current parameter values
        """
        n, m = x.shape
        bt = get_budget_topK(n, opts)

        if w is None:
            w = self.w

        if opts.unifprior:
            w_prior = self.w_unif_prior
        else:
            w_prior = w

        tau_rel = opts.constrainttype == AAD_CONSTRAINT_TAU_INSTANCE
        if (opts.detector_type == AAD_IFOREST or
                    opts.detector_type == AAD_HSTREES or
                    opts.detector_type == AAD_RSFOREST):
            w_new = self.forest_aad_weight_update(w, x, y, hf=append(ha, hn),
                                                  w_prior=w_prior, opts=opts, tau_rel=tau_rel,
                                                  linear=(self.ensemble_score == ENSEMBLE_SCORE_LINEAR))
        elif opts.detector_type == ATGP_IFOREST:
            w_soln = weight_update_iter_grad(x, y,
                                             hf=append(ha, hn),
                                             Ca=opts.Ca, Cn=opts.Cn, Cx=opts.Cx,
                                             topK=bt.topK, max_iters=1000)
            w_new = w_soln.w
        else:
            raise ValueError("Invalid weight update for IForest: %d" % opts.detector_type)
            # logger.debug("w_new:")
            # logger.debug(w_new)

        self.w = w_new

    def aad_learn_ensemble_weights_with_budget(self, ensemble, opts):

        if opts.budget == 0:
            return None

        x = ensemble.scores
        y = ensemble.labels

        n, m = x.shape
        bt = get_budget_topK(n, opts)

        metrics = get_alad_metrics_structure(opts.budget, opts)
        ha = []
        hn = []
        xis = []

        qstate = Query.get_initial_query_state(opts.qtype, opts=opts, qrank=bt.topK,
                                               a=1., b=1., budget=bt.budget)

        metrics.all_weights = np.zeros(shape=(opts.budget, m))

        w_unif_prior = self.get_uniform_weights(m)
        if self.w is None:
            self.w = w_unif_prior

        for i in range(bt.budget):

            starttime_iter = timer()

            # save the weights in each iteration for later analysis
            metrics.all_weights[i, :] = self.w
            metrics.queried = xis  # xis keeps growing with each feedback iteration

            order_anom_idxs, anom_score = self.order_by_score(x, self.w)

            if False and y is not None and metrics is not None:
                # gather AUC metrics
                metrics.train_aucs[0, i] = fn_auc(cbind(y, -anom_score))

                # gather Precision metrics
                prec = fn_precision(cbind(y, -anom_score), opts.precision_k)
                metrics.train_aprs[0, i] = prec[len(opts.precision_k) + 1]
                train_n_at_top = get_anomalies_at_top(-anom_score, y, opts.precision_k)
                for k in range(len(opts.precision_k)):
                    metrics.train_precs[k][0, i] = prec[k]
                    metrics.train_n_at_top[k][0, i] = train_n_at_top[k]

            xi_ = qstate.get_next_query(maxpos=n, ordered_indexes=order_anom_idxs,
                                        queried_items=xis,
                                        x=x, lbls=y, y=anom_score,
                                        w=self.w, hf=append(ha, hn),
                                        remaining_budget=opts.budget - i)
            # logger.debug("xi: %d" % (xi,))
            xi = xi_[0]
            xis.append(xi)
            metrics.test_indexes.append(qstate.test_indexes)

            if opts.single_inst_feedback:
                # Forget the previous feedback instances and
                # use only the current feedback for weight updates
                ha = []
                hn = []

            if y[xi] == 1:
                ha.append(xi)
            else:
                hn.append(xi)

            qstate.update_query_state(rewarded=(y[xi] == 1))

            if opts.batch:
                # Use the original (uniform) weights as prior
                # This is an experimental option ...
                w = self.w_unif_prior
                hf = order_anom_idxs[0:i]
                ha = hf[np.where(y[hf] == 1)[0]]
                hn = hf[np.where(y[hf] == 0)[0]]

            self.update_weights(x, y, ha=ha, hn=hn, opts=opts, w=self.w)

            if np.mod(i, 1) == 0:
                endtime_iter = timer()
                tdiff = difftime(endtime_iter, starttime_iter, units="secs")
                logger.debug("Completed [%s] fid %d rerun %d feedback %d in %f sec(s)" %
                             (opts.dataset, opts.fid, opts.runidx, i, tdiff))

        return metrics

    def run_aad(self, samples, labels, scores, w, opts):

        starttime_feedback = timer()

        agg_scores = self.get_score(scores, w)
        ensemble = Ensemble(samples, labels, scores, w,
                            agg_scores=agg_scores, original_indexes=np.arange(samples.shape[0]),
                            auc=0.0, model=None)

        metrics = alad_ensemble(ensemble, opts)

        num_seen = None
        num_seen_baseline = None
        queried_indexes = None
        queried_indexes_baseline = None

        if metrics is not None:
            save_alad_metrics(metrics, opts)
            num_seen, num_seen_baseline, queried_indexes, queried_indexes_baseline = \
                summarize_ensemble_num_seen(ensemble, metrics, fid=opts.fid)
            logger.debug("baseline: \n%s" % str([v for v in num_seen_baseline[0, :]]))
            logger.debug("num_seen: \n%s" % str([v for v in num_seen[0, :]]))

        endtime_feedback = timer()

        tdiff = difftime(endtime_feedback, starttime_feedback, units="secs")
        logger.debug("Processed [%s] file %d, auc: %f, time: %f sec(s); completed at %s" %
                     (opts.dataset, opts.fid, ensemble.auc, tdiff, endtime_feedback))

        return num_seen, num_seen_baseline, queried_indexes, queried_indexes_baseline

    def save_alad_metrics(self, metrics, opts):
        cansave = (opts.resultsdir != "" and os.path.isdir(opts.resultsdir))
        if cansave:
            save(metrics, filepath=opts.get_metrics_path())

    def load_alad_metrics(self, opts):
        metrics = None
        fpath = opts.get_metrics_path()
        canload = (opts.resultsdir != "" and os.path.isfile(fpath))
        if canload:
            # print "Loading metrics" + fpath
            metrics = load(fpath)
        else:
            print("Cannot load " + fpath)
        return metrics


def write_sparsemat_to_file(fname, X, fmt='%.18e', delimiter=','):
    if isinstance(X, np.ndarray):
        np.savetxt(fname, X, fmt='%3.2f', delimiter=",")
    elif isinstance(X, csr_matrix):
        f = open(fname, 'w')
        for i in range(X.shape[0]):
            a = X[i, :].toarray()[0]
            f.write(delimiter.join([fmt % v for v in a]))
            f.write(os.linesep)
            if (i + 1) % 10 == 0:
                f.flush()
        f.close()
    else:
        raise ValueError("Invalid matrix type")


def save_aad_model(filepath, model):
    f = gzip.open(filepath, 'wb')
    cPickle.dump(model, f, protocol=cPickle.HIGHEST_PROTOCOL)
    f.close()


def load_aad_model(filepath):
    f = gzip.open(filepath, 'rb')
    model = cPickle.load(f)
    f.close()
    return model


def get_forest_aad_args(dataset="", detector_type=AAD_IFOREST,
                        n_trees=100, n_samples=256,
                        forest_add_leaf_nodes_only=False,
                        forest_score_type=IFOR_SCORE_TYPE_CONST,
                        forest_max_depth=15,
                        ensemble_score=ENSEMBLE_SCORE_LINEAR,
                        Ca=100, Cx=0.001, sigma2=0.5,
                        budget=1, reruns=1, n_jobs=1, log_file="", plot2D=False,
                        streaming=False, stream_window=512, allow_stream_update=True):
    debug_args = [
        "--dataset=%s" % dataset,
        "--log_file=",
        "--querytype=%d" % QUERY_DETERMINISIC,
        "--detector_type=%d" % detector_type,
        "--constrainttype=%d" % AAD_CONSTRAINT_TAU_INSTANCE,
        # "--constrainttype=%d" % AAD_CONSTRAINT_NONE,
        "--withprior",
        "--unifprior",
        "--debug",
        "--sigma2=%f" % sigma2,
        "--Ca=%f" % Ca,
        "--Cx=%f" % Cx,
        "--ifor_n_trees=%d" % n_trees,  # for backward compatibility
        "--ifor_n_samples=%d" % n_samples,  # for backward compatibility
        "--forest_n_trees=%d" % n_trees,
        "--forest_n_samples=%d" % n_samples,
        "--forest_score_type=%d" % forest_score_type,
        "--forest_max_depth=%d" % forest_max_depth,
        "--ensemble_score=%d" % ensemble_score,
        "--budget=%d" % budget,
        "--reruns=%d" % reruns,
        "--n_jobs=%d" % n_jobs,
        "--runtype=%s" % ("multi" if reruns > 1 else "simple"),
        "--stream_window=%d" % (stream_window)
    ]
    if forest_add_leaf_nodes_only:
        debug_args.append("--forest_add_leaf_nodes_only")
    if plot2D:
        debug_args.append("--plot2D")
    if streaming:
        debug_args.append("--streaming")
    if allow_stream_update:
        debug_args.append("--allow_stream_update")

    # the reason to use 'debug=True' below is to have the arguments
    # read from the debug_args list and not commandline.
    args = get_command_args(debug=True, debug_args=debug_args)
    args.log_file = log_file
    configure_logger(args)
    return args
