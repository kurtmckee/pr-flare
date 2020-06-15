'''
:class:`MappedGaussianProcess` uses splines to build up interpolation\
function of the low-dimensional decomposition of Gaussian Process, \
with little loss of accuracy. Refer to \
`Vandermause et al. <https://www.nature.com/articles/s41524-020-0283-z>`_, \
`Glielmo et al. <https://journals.aps.org/prb/abstract/10.1103/PhysRevB.97.184307>`_
'''
import time, os, math, inspect, subprocess, json, warnings, pickle
import numpy as np
import multiprocessing as mp

from copy import deepcopy
from typing import List

from flare.env import AtomicEnvironment
from flare.gp import GaussianProcess
from flare.utils.element_coder import NumpyEncoder, element_to_Z, Z_to_element

from flare.mgp.map2b import Map2body
from flare.mgp.map3b import Map3body
from flare.mgp.utils import str_to_mapped_kernel

class MappedGaussianProcess:
    '''
    Build Mapped Gaussian Process (MGP)
    and automatically save coefficients for LAMMPS pair style.

    Args:
        grid_params (dict): Parameters for the mapping itself, such as
            grid size of spline fit, etc. As described below.
        unique_species (dict): List of all the (unique) species included during
            the training that need to be mapped
        map_force (bool): if True, do force mapping; otherwise do energy mapping,
            default is False
        GP (GaussianProcess): None or a GaussianProcess object. If a GP is input,
            and container_only is False, automatically build a mapping corresponding
            to the GaussianProcess.
        mean_only (bool): if True: only build mapping for mean (force)
        container_only (bool): if True: only build splines container
            (with no coefficients); if False: Attempt to build map immediately
        lmp_file_name (str): LAMMPS coefficient file name
        n_cpus (int): Default None. Set to the number of cores needed for
            parallelization. Used in the construction of the map.
        n_sample (int): Default 100. The batch size for building map. Not used now.

    Examples:

    >>> # build 2 + 3 body map
    >>> grid_params = {'twobody': {'grid_num': [64]},
    ...                'threebody': {'grid_num': [64, 64, 64]}}

    For `grid_params`, the following keys and values are allowed

    Args:
        'two_body' (dict, optional): if 2-body is present, set as a dictionary
            of parameters for 2-body mapping. Parameters see below.
        'three_body' (dict, optional): if 3-body is present, set as a dictionary
            of parameters for 3-body mapping. Parameters see below.
        'load_grid' (str, optional): Default None. the path to the directory
            where the previously generated grids (``grid_*.npy``) are stored.
            If no path is specified, MGP will construct grids from scratch.
        'lower_bound_relax' (float, optional): Default 0.1. if 'lower_bound' is
            set to 'auto' this value will be used as a relaxation of lower
            bound. (see below the description of 'lower_bound')

    For two/three body parameter dictionary, the following keys and values are allowed

    Args:
        'grid_num' (list): a list of integers, the number of grid points for
            interpolation. The larger the number, the better the approximation
            of MGP is compared with GP.
        'lower_bound' (str or list, optional): Default 'auto', the lower bound
            of the spline interpolation will be searched. First, search the
            training set of GP and find the minimal interatomic distance r_min.
            Then, the ``lower_bound = r_min - lower_bound_relax``. The user
            can set their own lower_bound, of the same shape as 'grid_num'.
            E.g. for threebody, the customized lower bound can be set as
            [1.2, 1.2, 1.2].
        'upper_bound' (str or list, optional): Default 'auto', the upper bound
            of the spline interpolation will be the cutoffs of GP. The user
            can set their own upper_bound, of the same shape as 'grid_num'.
            E.g. for threebody, the customized lower bound can be set as
            [3.5, 3.5, 3.5].
        'svd_rank' (int, optional): Default 'auto'. If the variance mapping is
            needed, it is set as the rank of the mapping. 'auto' uses full
            rank, which is the smaller one between the total number of grid
            points and training set size. i.e.
            ``full_rank = min(np.prod(grid_num), 3 * N_train)``
    '''

    def __init__(self,
                 grid_params: dict,
                 unique_species: list=[],
                 map_force: bool=False,
                 GP: GaussianProcess=None,
                 mean_only: bool=True,
                 container_only: bool=True,
                 lmp_file_name: str='lmp.mgp',
                 n_cpus: int=None,
                 n_sample: int=100):

        # load all arguments as attributes
        self.map_force = map_force
        self.mean_only = mean_only
        self.lmp_file_name = lmp_file_name
        self.n_cpus = n_cpus
        self.n_sample = n_sample
        self.grid_params = grid_params
        self.species_labels = []
        self.coded_species = []
        self.hyps_mask = None
        self.cutoffs = None
        self.GP = GP

        for i, ele in enumerate(unique_species):
            if isinstance(ele, str):
                self.species_labels.append(ele)
                self.coded_species.append(element_to_Z(ele))
            elif isinstance(ele, int):
                self.coded_species.append(ele)
                self.species_labels.append(Z_to_element(ele))

        if (GP is not None):
            self.hyps_mask = GP.hyps_mask
            self.cutoffs = GP.cutoffs

        if 'load_grid' not in grid_params:
            grid_params['load_grid']= None
        if 'update' not in grid_params:
            grid_params['update'] = False
        if 'lower_bound_relax' not in grid_params:
            grid_params['lower_bound_relax'] = 0.1

        self.maps = {}

        optional_xb_params = ['lower_bound', 'upper_bound', 'svd_rank', 'lower_bound_relax']
        for key in grid_params:
            if 'body' in key:
                if 'twobody' == key:
                    mapxbody = Map2body
                elif 'threebody' == key:
                    mapxbody = Map3body
                else:
                    raise KeyError("Only 'twobody' & 'threebody' are allowed")

                xb_dict = grid_params[key]

                # set to 'auto' if the param is not given
                args = {}
                for oxp in optional_xb_params:
                    args[oxp] = xb_dict.get(oxp, 'auto')
                args['grid_num'] = xb_dict.get('grid_num', None)

                for k in xb_dict:
                    args[k] = xb_dict[k]

                xb_maps = mapxbody(**args, **self.__dict__)
                self.maps[key] = xb_maps

    def build_map(self, GP):

        self.hyps_mask = GP.hyps_mask
        self.cutoffs = GP.cutoffs

        for xb in self.maps:
            self.maps[xb].build_map(GP)

        # write to lammps pair style coefficient file
        self.write_lmp_file(self.lmp_file_name)


    def predict(self, atom_env: AtomicEnvironment, mean_only: bool = False,
            ) -> (float, 'ndarray', 'ndarray', float):
        '''
        predict force, variance, stress and local energy for given
        atomic environment

        Args:
            atom_env: atomic environment (with a center atom and its neighbors)
            mean_only: if True: only predict force (variance is always 0)

        Return:
            force: 3d array of atomic force
            variance: 3d array of the predictive variance
            stress: 6d array of the virial stress
            energy: the local energy (atomic energy)
        '''

        force = virial = kern = v = energy = 0
        for xb in self.maps:
            pred = self.maps[xb].predict(atom_env, mean_only)
            force += pred[0]
            virial += pred[1]
            kern += pred[2]
            v += pred[3]
            energy += pred[4]

        variance = kern - np.sum(v**2, axis=0)

        return force, variance, virial, energy


    def write_lmp_file(self, lammps_name):
        '''
        write the coefficients to a file that can be used by lammps pair style
        '''

        f = open(lammps_name, 'w')

        # write header
        header_comment = '''# #2bodyarray #3bodyarray\n# elem1 elem2 a b order\n\n'''
        f.write(header_comment)
        header = ''
        xbodies = ['twobody', 'threebody']
        for xb in xbodies:
            if xb in self.maps:
                num = len(self.maps[xb].maps)
            else:
                num = 0
            header += f'{num} '
        f.write(header + '\n')

        # write coefficients
        for xb in self.maps:
            self.maps[xb].write(f)

        f.close()

    def as_dict(self) -> dict:
        """
        Dictionary representation of the MGP model.
        """

        out_dict = deepcopy(dict(vars(self)))
        out_dict.pop('maps')

        # Uncertainty mappings currently not serializable;
        if not self.mean_only:
            warnings.warn("Uncertainty mappings cannot be serialized, "
                          "and so the MGP dict outputted will not have "
                          "them.", Warning)
            out_dict['mean_only'] = True

        # only save the coefficients
        maps_dict = {}
        for m in self.maps:
            maps_dict[m] = self.maps[m].as_dict()
        out_dict['maps'] = maps_dict

        return out_dict

    @staticmethod
    def from_dict(dictionary: dict):
        """
        Create MGP object from dictionary representation.
        """

        # Set GP
        if dictionary.get('GP'):
            GP = GaussianProcess.from_dict(dictionary.get("GP"))
        else:
            dictionary['GP'] = None

        dictionary['unique_species'] = list(set(dictionary['species_labels']))
        if 'container_only' not in dictionary:
            dictionary['container_only'] = True

        init_arg_name = ['grid_params', 'unique_species', 'map_force', 'GP',
            'mean_only', 'container_only', 'lmp_file_name', 'n_cpus', 'n_sample']
        kwargs = {key: dictionary[key] for key in init_arg_name}
        new_mgp = MappedGaussianProcess(**kwargs)

        # Fill up the model with the saved coeffs
        if 'twobody' in new_mgp.maps:
            new_mgp.maps['twobody'] = Map2body.from_dict(dictionary['maps']['twobody'], Map2body)
        if 'threebody' in new_mgp.maps:
            new_mgp.maps['threebody'] = Map3body.from_dict(dictionary['maps']['threebody'], Map3body)

        return new_mgp

    def write_model(self, name: str, format='json'):
        """
        Write everything necessary to re-load and re-use the model
        :param model_name:
        :return:
        """
        if 'json' in format.lower():
            with open(f'{name}.json', 'w') as f:
                json.dump(self.as_dict(), f, cls=NumpyEncoder)

        elif 'pickle' in format.lower() or 'binary' in format.lower():
            with open(f'{name}.pickle', 'wb') as f:
                pickle.dump(self, f)

        else:
            raise ValueError("Requested format not found.")



    @staticmethod
    def from_file(filename: str):
        if '.json' in filename:
            with open(filename, 'r') as f:
                model = \
                    MappedGaussianProcess.from_dict(json.loads(f.readline()))
            return model

        elif 'pickle' in filename:
            with open(filename, 'rb') as f:
                return pickle.load(f)
        else:
            raise NotImplementedError


