# Copyright 2016 Espen Flage-Larsen
#
#    This file is part of T4ME.
#
#    T4ME is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    T4ME is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with T4ME.  If not, see <http://www.gnu.org/licenses/>.

#!/usr/bin/python
"""Contains routines to setup the lattice."""

# pylint: disable=useless-import-alias, too-many-arguments, invalid-name, too-many-statements, too-many-lines, global-statement, unsubscriptable-object

import sys
import math
import logging
import copy
import numpy as np

import t4me.utils as utils
import t4me.constants as constants
import t4me.interface as interface
import t4me.spglib_interface as spglib_interface  # pylint: disable=import-error, no-name-in-module


class Lattice():  # pylint: disable=too-many-instance-attributes
    """
    Contains routines to set up the system atmosphere.

    This includes the unit cell, the BZ and IBZ k-point mesh etc.

    Read the YAML cell configuration file (cellparams.yml by
    default is used to read basic cell parameters if for instance
    this is not set up by other inputs in the :mod:`interface`)

    Parameters
    ----------
    param : object
        A `Param()` object containing the parameters of the
        general configuration file.

    """

    def __init__(self, param, location=None, filename=None):
        self.param = param
        self.unitcell = None
        self.runitcell = None
        self.positions = None
        self.species = None
        self.volume = None
        self.rvolume = None
        self.spg_kmesh = None
        self.kdata = self.Kmesh()
        if self.param.read == "param" or self.param.read[:5] == "numpy":
            interface.lattice_param_numpy(
                self, location=location, filename=filename)
        elif self.param.read == "vasp":
            interface.lattice_vasp(self, location=location, filename=filename)
        elif self.param.read == "w90":
            interface.lattice_w90(self)
        else:
            logging.info(
                "Not setting up the the lattice in a traditional manner.")

        # these are always present (as long as interface is
        # constructred properly), set internals
        self.kmesh = self.kdata.mesh
        self.kmesh_ired = self.kdata.mesh_ired
        self.mapping_bz_to_ibz = self.kdata.mapping_bz_to_ibz
        self.mapping_ibz_to_bz = self.kdata.mapping_ibz_to_bz
        self.ksampling = self.kdata.sampling
        self.ibz_weights = self.kdata.ibz_weights
        self.k_sort_index = self.kdata.k_sort_index
        self.borderless = self.kdata.borderless

        # this one do nothing if both BZ, IBZ, mappings and weights are set
        # otherwise, it generates a new grid to set up these parameters
        # and crashes if it detects discrepancies
        if not self.param.work_on_full_grid:
            self.generate_consistent_mesh()

        # calculate unit cell volume
        self.volume = calculate_cell_volume(self.unitcell)

        # calculate the recirpocal unit cell
        self.runitcell = self.real_to_rec()

        # calculate the reciprocal unit cell volume
        self.rvolume = calculate_cell_volume(self.runitcell)

        # check if unitcell is regular
        self.regular = self.regularcell()

        # check that we have at least set a minimal set
        # of parameters
        self.check_lattice()

    class Kmesh():  # pylint: disable=too-few-public-methods
        """
        Data container for the k-point mesh generation.

        Currently not used throughout the program, only for the intial setup.

        .. todo:: Incorporate this class into the whole program.

        """

        def __init__(self):
            self.mesh = None
            self.mesh_ired = None
            self.mapping_bz_to_ibz = None
            self.mapping_ibz_to_bz = None
            self.sampling = None
            self.ibz_weights = None
            self.k_sort_index = None
            self.borderless = None

    def generate_consistent_mesh(self):
        """
        Calculates the k-point mesh and sets up proper mapping.

        Also makes sure the IBZ or BZ supplied is similar to
        the one generated by spglib given the symmetry used.

        Parameters
        ----------
        None

        Returns
        -------
        None

        Notes
        -----
        This should be reconsidered in the future as this is
        bound to give the user problems. Consider writing an
        interface which can accept the symmetry operators and use
        these directly.

        """

        # set logger
        logger = logging.getLogger(sys._getframe().f_code.co_name)  # pylint: disable=protected-access
        logger.debug("Running generate_consistent_mesh.")
        if ((self.mapping_bz_to_ibz is None)
                or (self.mapping_ibz_to_bz is None)):
            if (self.kmesh is None) and (self.kmesh_ired is not None):
                # have IBZ, generate BZ
                kmesh = copy.deepcopy(self.kmesh_ired)
                self.create_kmesh(borderless=True)
                if kmesh.shape[0] != self.kmesh_ired.shape[0]:
                    logger.error(
                        "The numbers of IBZ points does not correspond "
                        "to the IBZ points generated by spglib. Exiting.")
                    sys.exit(1)
                if not np.allclose(
                        kmesh, self.kmesh_ired, atol=self.param.symprec):
                    logger.error(
                        "The IBZ points does not correspond to the IBZ "
                        "points generated with spglib within symprec set "
                        "at %s. Exiting.", str(self.param.symprec))
                    logger.error("IBZ: %s\n IBZ internal: %s", str(kmesh),
                                 str(self.kmesh_ired))
                    sys.exit(1)
            elif (self.kmesh_ired is None) and (self.kmesh is not None):
                # have BZ, generate IBZ
                kmesh = copy.deepcopy(self.kmesh)
                self.create_kmesh(borderless=True)
                if kmesh.shape[0] != self.kmesh.shape[0]:
                    logger.error(
                        "The numbers of BZ points does not correspond "
                        "to the BZ points generated by spglib. Exiting.")
                    sys.exit(1)
                if not np.allclose(kmesh, self.kmesh, atol=self.param.symprec):
                    logger.error(
                        "The BZ points does not correspond to the BZ "
                        "points generated with spglib within symprec "
                        "set at %s. Exiting.", str(self.param.symprec))
                    logger.error("BZ: %s\n BZ internal: %s", str(kmesh),
                                 str(self.kmesh))
                    sys.exit(1)
            elif (self.kmesh is None) and (self.kmesh_ired is None):
                self.create_kmesh(borderless=True)

        # check that we at least have data present for the
        # essentials
        self.check_mesh()

    def check_lattice(self):  # noqa: MC0001
        """
        Checks if the celldata is present and that the most important parameters are defined.

        Parameters
        ----------
        None

        Returns
        -------
        None

        """

        # set logger
        logger = logging.getLogger(sys._getframe().f_code.co_name)  # pylint: disable=protected-access
        logger.debug("Running check_lattice.")

        if self.unitcell is None:
            logger.error("The entry unitcell is still None. Exiting")
            sys.exit(1)

        if self.runitcell is None:
            logger.error("The entry runitcell is still None. Exiting")
            sys.exit(1)

        if self.positions is None:
            logger.error("The entry positions is still None. Exiting")
            sys.exit(1)

        if self.species is None:
            logger.error("The entry species is still None. Exiting")
            sys.exit(1)

        if self.volume is None:
            logger.error("The entry volume is still None. Exiting")
            sys.exit(1)

        if self.rvolume is None:
            logger.error("The entry rvolume is still None. Exiting")
            sys.exit(1)

    def check_mesh(self):  # noqa: MC0001
        """
        Checks that the most important parameters for the k-point mesh have been set.

        Parameters
        ----------
        None

        Returns
        -------
        None

        """

        # set logger
        logger = logging.getLogger(sys._getframe().f_code.co_name)  # pylint: disable=protected-access
        logger.debug("Running check_mesh")

        if self.kmesh is None:
            logger.error("The entry kmesh is still None. Exiting")
            sys.exit(1)

        if self.kmesh_ired is None:
            logger.error("The entry kmesh_ired is still None. Exiting")
            sys.exit(1)

        if self.ksampling is None:
            logger.error("The entry ksampling is still None. Exiting")
            sys.exit(1)

        # when we read in data from VASP etc. for the full grid
        # (i.e. if KINTER and LVEL is set we really do not need the
        # mapping between the ibz and bz or the weights, in this case
        # do not halt,
        # in the future modify VASP to also eject the mapping table
        # to the vasprun.xml file when KINTER and LVEL is set such
        # that we can reenable this test.)
        try:
            if not self.param.vasp_lvel:
                if self.mapping_bz_to_ibz is None:
                    logger.error(
                        "The entry mapping_bz_to_ibz is still None. Exiting")
                    sys.exit(1)
                if self.mapping_ibz_to_bz is None:
                    logger.error(
                        "The entry mapping_ibz_to_bz is still None. Exiting")
                    sys.exit(1)
                if self.ibz_weights is None:
                    logger.error(
                        "The entry ibz_weights is still None. Exiting")
                    sys.exit(1)
        except AttributeError:
            pass

    def fetch_kmesh(self, direct=True, ired=False):
        """
        Calculates the k point mesh in direct or cartersian coordinates.

        Parameters
        ----------
        direct : boolean
            Selects the k-point grid in direct (True) or cartesian
            coordinates (False)
        ired : boolean
            Selects the ireducible k-point grid (True),
            or the full grid (False)

        Returns
        -------
        ndarray
            | Dimension: (M,3)

            Contains M k-points representing the k-point mesh.

        """
        # set logger
        logger = logging.getLogger(sys._getframe().f_code.co_name)  # pylint: disable=protected-access
        logger.debug("Running fetch_kmesh.")

        if direct:
            if ired:
                return self.kmesh_ired
            return self.kmesh

        if ired:
            return self.dir_to_cart(self.kmesh_ired)

        return self.dir_to_cart(self.kmesh)

    def fetch_kmesh_unit_vecs(self, direct=True):
        """
        Calculates the k-point mesh sampling points along the unit vectors.

        Works in direct or cartesian coordinates.

        Parameters
        ----------
        direct : boolean
            Selects to return direct (True) or cartesian
            (False) unit vectors.

        Returns
        -------
        ndarray
            | Dimension: (3)

            The unit vectors of the k-point mesh.

        """

        # set logger
        logger = logging.getLogger(sys._getframe().f_code.co_name)  # pylint: disable=protected-access
        logger.debug("Running fetch_kmesh_unit_vecs.")

        symprec = self.param.symprec

        if direct:
            kmesh_unit_vec = np.array([
                np.unique(np.floor(
                    self.kmesh[:, 0] / symprec).astype(int)) * symprec,
                np.unique(np.floor(
                    self.kmesh[:, 1] / symprec).astype(int)) * symprec,
                np.unique(np.floor(self.kmesh[:, 2] / symprec).astype(int)) *
                symprec
            ])
        else:
            # this only makes sense in direct mode for grids that are not
            # regular as we would have many more unique points than the
            # base k-point sampling due to to the shifted coordinate axes
            # if not self.regular:
            #    logger.error("Do not call this routine if the grid is "
            #                 "not regular. Exiting.")
            #    # sys.exit(1)
            kmesh_cart = self.dir_to_cart(self.kmesh)
            kmesh_unit_vec = np.array([
                np.unique(np.floor(
                    kmesh_cart[:, 0] / symprec).astype(int)) * symprec,
                np.unique(np.floor(
                    kmesh_cart[:, 1] / symprec).astype(int)) * symprec,
                np.unique(np.floor(kmesh_cart[:, 2] / symprec).astype(int)) *
                symprec
            ])
        return kmesh_unit_vec

    def fetch_ksampling_from_stepsize(self, step_sizes):
        """
        Calculates the ksampling based on the step size.

        Parameters
        ----------
        step_sizes : ndarray
            | Dimension: (3)

            The step size along each reciprocal unit vector

        Returns
        -------
        ksampling : float, float, float
            The suggested sampling along each reciprocal unit vector

        """

        # fetch the length in AA of the reciprocal unit vectors
        lengths = self.fetch_length_runitcell_vecs()

        # ceil to make sure we at least have this step size
        ksampling = np.ceil(lengths / step_sizes).astype(int)

        # also make sure the grid is odd
        for i in range(3):
            if utils.is_even(ksampling[i]):
                ksampling[i] = ksampling[i] + 1

        return ksampling

    def check_for_duplicate_points(self):
        """
        Checks for duplicate k-points. This is currently not supported.

        Parameters
        ----------
        None

        Returns
        -------
        None

        """

        # set logger
        logger = logging.getLogger(sys._getframe().f_code.co_name)  # pylint: disable=protected-access

        # cutoff for symmetry detection, differences outside this
        # is irrelevant and should be considered the same points
        symprec = self.param.symprec

        # convert to int within symprec
        kmesh_int = np.floor(self.kmesh / symprec).astype(int)

        # check the shape of the uniques
        kmesh_int = np.ascontiguousarray(kmesh_int)
        unique_kmesh = np.unique(
            kmesh_int.view([('', kmesh_int.dtype)] * kmesh_int.shape[1]))

        unique_shape = unique_kmesh.view(kmesh_int.dtype).reshape(
            (unique_kmesh.shape[0], kmesh_int.shape[1])).shape

        # do checks
        if unique_shape != kmesh_int.shape:
            logger.error(
                "Duplicate k-points was detected "
                "using a symprec of %s. "
                "Exiting.", str(symprec))
            sys.exit(1)

    def fetch_length_runitcell_vecs(self):
        """
        Returns the length of each reciprocal lattice vector.

        Parameters
        ----------
        None

        Returns
        -------
        ndarray
            | Dimension: (3)

            The lenght of each reciprocal lattice vector in
            inverse AA.

        """

        return np.array([
            np.linalg.norm(self.runitcell[0]),
            np.linalg.norm(self.runitcell[1]),
            np.linalg.norm(self.runitcell[2])
        ])

    def fetch_kmesh_step_size(self, direct=False):
        """
        Returns the step size along each direction.

        Parameters
        ----------
        direct : boolean, optional
            If True the step size is returned in direct coordinates,
            otherwise it is returned in AA^{-1} units. Defaults to
            False.

        Returns
        -------
        stepx, stepy, stepz : float, float, float
            The step size along each reciprocal lattice vector.

        Notes
        -----
        Regularly spaced and ordered grids are assumed. Also, the
        step size returned is with respect to the reciprocal unit
        cells unit vectors. If `direct` is True the step size between
        0 and 1 is returned, while for False, this step size is
        scaled by the length of the reciprocal unit vectors
        in :math:`AA^{-1}`.

        """

        # set logger
        logger = logging.getLogger(sys._getframe().f_code.co_name)  # pylint: disable=protected-access

        # check for duplicate points
        self.check_for_duplicate_points()

        # no duplicates detected, calculate step size
        shiftx = self.ksampling[2] * self.ksampling[1]
        shifty = self.ksampling[2]
        kmesh = self.fetch_kmesh(direct=True)

        stepx = kmesh[shiftx, 0] - kmesh[0, 0]
        stepy = kmesh[shifty, 1] - kmesh[0, 1]
        stepz = kmesh[1, 2] - kmesh[0, 2]
        # check that no stepping is zero inside symprec
        if (stepx < self.param.symprec) or \
           (stepy < self.param.symprec) or \
           (stepz < self.param.symprec):
            logger.error("The k-point step size is zero in one "
                         "of the directions. Are you sure the grid "
                         "is regular? Exiting.")
            sys.exit(1)
        if direct:
            return stepx, stepy, stepz
        # fetch length of reciprocal lattice vectors
        lengths = self.fetch_length_runitcell_vecs()
        return stepx * lengths[0], stepy * lengths[1], \
            stepz * lengths[2]

    def fetch_kpoints_along_line(self, kstart, kend, stepping, direct=True):
        """
        Calculates the k - points along a line in the full BZ k - point mesh

        Parameters
        ----------
        kstart: ndarray
            | Dimension: (3)

            The start k - point in direct coordinates.
        kend: ndarray
            | Dimension: (3)

            The end k - point in direct coordinates.
        stepping: int
            The N number of steps along the line.
        direct: boolean
            If True direct coordinates are returned, else
            cartesian coordinates are returned.

        Returns
        -------
        ndarray
            | Dimension: (N)

            The N number of k - point cartesian coordinates
            along the line.

        """

        # set logger
        logger = logging.getLogger(sys._getframe().f_code.co_name)  # pylint: disable=protected-access
        logger.debug("Running fetch_kpoints_along_line.")
        sampx = np.linspace(kstart[0], kend[0], stepping)
        sampy = np.linspace(kstart[1], kend[1], stepping)
        sampz = np.linspace(kstart[2], kend[2], stepping)
        kx, ky, kz = np.meshgrid(sampx, sampy, sampz, sparse=True)
        kpts = np.vstack([kx.ravel(), ky.ravel(), kz.ravel()]).T
        # return
        if direct is False:
            kpts = self.dir_to_cart(kpts)
        return kpts

    def rec_to_real(self, unitcell=None):
        r"""
        Calculates the real unitcell from the reciprocal unitcell.

        Parameters
        ----------
        unitcell: ndarray, optional
            | Dimension: (3, 3)

            A reciprocal unitcell. Defaults to the internal
            runitcell for the current `Lattice()` object.

        Returns
        -------
        ndarray
            | Dimension: (3, 3)

            The real unitcell, with: math: `\\vec{a_1}` = [0][:] etc.

        """

        # set logger
        logger = logging.getLogger(sys._getframe().f_code.co_name)  # pylint: disable=protected-access
        logger.debug("Running rec_to_real.")

        if unitcell is None:
            unitcell = self.runitcell

        return 2 * math.pi * np.linalg.inv(unitcell.transpose())

    def real_to_rec(self, unitcell=None):
        r"""
        Calculates the reciprocal unitcell from the real unitcell.

        Parameters
        ----------
        unitcell: ndarray, optional
            | Dimension: (3, 3)

            A real unitcell. Defaults to the unitcell for the
            current `Lattice()` object.

        Returns
        -------
        ndarray
            | Dimension: (3, 3)

            The reciprocal unitcell, with: math: `\\vec{b_1}` = [0][:]
            etc.

        """

        # set logger
        logger = logging.getLogger(sys._getframe().f_code.co_name)  # pylint: disable=protected-access
        logger.debug("Running real_to_rec.")

        if unitcell is None:
            unitcell = self.unitcell

        ainv = 2 * math.pi * np.linalg.inv(unitcell)
        return np.ascontiguousarray(ainv.transpose(), dtype='double')

    def dir_to_cart(self, v, real=False):
        r"""
        Calculates the cartesian vector if the input is a vector in direct coordinates.

        Parameters
        ----------
        v: ndarray
            | Dimension: (3)

            The supplied cartesian vector.
        real: boolean
            If set to False, the reciprocal unitcell is used,
            is set to True, the unitcell is used.

        Returns
        -------
        ndarray
            | Dimension: (3)

            The direct vector.

        Notes
        -----
            Typically the transformation in reciprocal space is

            .. math::\\vec{k}B=\\vec{k}',

            .. math::\\vec{k}=\\vec{k}'B^{-1},

            where :math:`\\vec{k}` and :math:`\\vec{k}'` is the
            reciprocal vector in direct and cartesian coordinate
            systems, respectively. Here, B is the reciprocal unit
            cell.

        """

        # set logger
        logger = logging.getLogger(sys._getframe().f_code.co_name)  # pylint: disable=protected-access
        logger.debug("Running dir_to_cart.")

        if not real:
            cart = np.dot(v, self.runitcell)
        else:
            cart = np.dot(v, self.unitcell)

        return cart

    def cart_to_dir(self, v, real=False):
        r"""
        Calculates the direct vector if the input is a vector in cartesian coordinates.

        Parameters
        ----------
        v: ndarray
            | Dimension: (3)

            The input direct vector.
        real: boolean
            If set to False, the reciprocal unitcell is used,
            is set to True, the unitcell is used.

        Returns
        -------
        ndarray
            | Dimension: (3)

            The cartesian vector.

        Notes
        -----
            Typically the transformation in reciprocal space is

            .. math::\\vec{k}B=\\vec{k}',

            .. math::\\vec{k}=\\vec{k}'B^{-1},

            where :math:`\\vec{k}` and :math:`\\vec{k}'` is the
            reciprocal vector in direct and cartesian coordinate
            systems, respectively. Here, B is the reciprocal unit
            cell.

        """

        # set logger
        logger = logging.getLogger(sys._getframe().f_code.co_name)  # pylint: disable=protected-access
        logger.debug("Running cart_to_dir.")

        if not real:
            direct = np.dot(v, np.linalg.inv(self.runitcell))
        else:
            direct = np.dot(v, np.linalg.inv(self.unitcell))
        return direct

    def fetch_bz_border(self, kmesh=None, direct=True):
        """
        Returns the BZ border in direct or cartesian coordinates

        Parameters
        ----------
        kmesh: ndarray, optional
            | Dimension: (N, 3)

            The k - point mesh for N k - points. If not supplied,
            the value of the current `Lattice()` is used.
        direct: boolean, optional
            Selects if direct coordinates are to be returned
            (True, default) or cartesian(False).

        Returns
        -------
        ndarray
            | Dimension: (3)

            Contains the BZ border points(largest coordinate along
            each axis).

        """

        # set logger
        logger = logging.getLogger(sys._getframe().f_code.co_name)  # pylint: disable=protected-access
        logger.debug("Running fetch_bz_border.")

        if kmesh is None:
            kmesh = self.kmesh
        if direct is False:
            kmesh = self.dir_to_cart(kmesh)
        border = np.zeros(6)
        for i in range(3):
            border[2 * i] = np.amin(kmesh[:, i])
            border[2 * i + 1] = np.amax(kmesh[:, i])
        return border

    def create_kmesh(  # pylint: disable=too-many-locals # noqa: MC0001
            self,
            ksampling=None,
            shift=np.array([0, 0, 0], dtype='intc'),
            halfscale=True,
            borderless=False):
        """
        Returns the k point mesh.

        Parameters
        ----------
        ksampling: ndarray, optional
            | Dimension: (3)

            Contains the k - point sampling points along each direction.
            If not supplied the `ksampling` of the current `Lattice()`
            object is used.
        shift: ndarray, optional
            | Dimension: (3)

            Contains the shift for the k - point mesh generation.
            If not supplied the default is set to[0.0, 0.0, 0.0]
        halfscale: boolean
            Selects if the BZ mesh should go from -0.5 to 0.5 or
            -1.0 to 1.0. If not supplied, the default is set to True.
        borderless: boolean
            Selects if the BZ border points should be included in
            the mesh generation. True selects borderless, e.g.
            -0.5, 0.5 etc. are excluded.

        Returns
        -------
        mapping_bz_to_ibz: ndarray
            | Dimension: (N, 3)

            Contains a mapping table such that it is possible to go
            from the BZ to the IBZ mesh. Stored in the current
            `Lattice()` object.
        mapping_ibz_to_bz: ndarray
            | Dimension: (M, 3)

            Contains a mapping table such that it is possible to go
            from the IBZ to the BZ mesh. Stored in the current
            `Lattice()` object.
        kmesh: ndarray
            | Dimension: (N, 3)

            The k - point mesh in the full BZ for N sampling points
            determined by the multiplication of the content of
            `ksampling`. Stored in the current `Lattice()` object.
        kmesh_ibz: ndarray
            | Dimension: (M, 3)

            The k - point mesh in the irreducible BZ. The number of
            points M is dependent on the symmetry. Usually M < N.
            Stored in the current `Lattice()` object.
        ksampling: ndarray
            | Dimension: (3)

            The full BZ k - point sampling in each direction.
            Stored in the current `Lattice()` object.

        Notes
        -----
        This routines use spglib, an excellent tool written by A. Togo
        : cite: `spglib`

        .. rubric: : References

        .. bibliography: : references.bib
           : style: unsrt
           : filter: docname in docnames

        """

        # set logger
        logger = logging.getLogger(sys._getframe().f_code.co_name)  # pylint: disable=protected-access
        logger.debug("Running create_kmesh.")

        if ksampling is None:
            try:
                ksampling = self.ksampling
            except AttributeError:
                logger.error("No ksampling is set. Exiting.")
                sys.exit(1)
        # check for gamma only
        if self.param.gamma_center:
            # up even ksampling grid to odd number
            if utils.is_even(ksampling[0]):
                ksampling[0] = ksampling[0] + 1
            if utils.is_even(ksampling[1]):
                ksampling[1] = ksampling[1] + 1
            if utils.is_even(ksampling[2]):
                ksampling[2] = ksampling[2] + 1
        else:
            logger.info(
                "Please set gamma centered to True and supply/generate "
                "a gamma centered grid.")
            sys.exit(1)
        # make sure ksampling have correct dtype
        ksampling = np.ascontiguousarray(ksampling, dtype='intc')
        # same for the number of kpoints
        k = np.zeros((np.prod(ksampling), 3), dtype="intc")

        if not self.param.work_on_full_grid:
            # need the IBZ and the BZ-IBZ mappings
            # and the mappings
            spg_mapping = np.zeros(np.prod(ksampling), dtype="intc")
            logger.info(
                "Calling Spglib to set up the k-point grid with a "
                "sampling of %sx%sx%s.", str(ksampling[0]), str(ksampling[1]),
                str(ksampling[2]))
            if self.borderless:
                borderless = True
            if borderless:
                logger.info(
                    "Running borderless compatible k-point generation etc. "
                    "(VASP, SKW, W90, etc.)")
                # the international symbol returned from spglib
                intsym = spglib_interface.get_reciprocal_mesh(
                    ksampling,
                    np.ascontiguousarray(self.unitcell.T),
                    self.positions,
                    self.species,
                    shift,
                    k,
                    spg_mapping,
                    is_time_reversal=False,
                    symprec=self.param.symprec)
            else:
                # the international symbol returned from spglib
                intsym = spglib_interface.get_reciprocal_mesh(
                    ksampling,
                    np.ascontiguousarray(self.unitcell.T),
                    self.positions,
                    self.species,
                    shift,
                    k,
                    spg_mapping,
                    is_time_reversal=True,
                    symprec=self.param.symprec)
            logger.info(
                "Spglib detected the symmetry %s with symprec set to %s",
                str(intsym), str(self.param.symprec))
            # build array for IBZ
            k_ired = k[np.unique(spg_mapping, return_index=True)[1]]
            # sort mesh
            k_sort_ired = utils.fetch_sorting_indexes(k_ired)
            k_sort_full = utils.fetch_sorting_indexes(k)
            kmesh_ired = k_ired[k_sort_ired]
            kmesh_full = k[k_sort_full]

            # store original SPGLIB mesh for use later
            # (e.g. tetrahedron method)
            self.spg_kmesh = k[k_sort_full]

            shuffle = np.zeros(spg_mapping.shape[0], dtype=np.intc)
            for index, value in enumerate(spg_mapping):
                shuffle[index] = np.where(k_sort_full == value)[0][0]
            # build ired and sort
            k_ired = kmesh_full[shuffle[np.sort(
                np.unique(shuffle, return_index=True)[1])]]
            k_sort_ired = utils.fetch_sorting_indexes(k_ired)
            k_ired = k_ired[k_sort_ired]

            mapping_ibz_to_bz = shuffle[k_sort_full]
            mapping_bz_to_ibz = np.zeros(mapping_ibz_to_bz.size, dtype=np.intc)
            for index, ibz_point in enumerate(np.unique(mapping_ibz_to_bz)):
                mask = np.in1d(mapping_ibz_to_bz, ibz_point)
                np.copyto(mapping_bz_to_ibz, index, where=mask)

            # scale the grid from integer to float (consider to postpone this
            # in the future...e.g. integers are nice because test for uniqueness
            # is exact etc.)
            if borderless:
                scaling = 1.0 / ksampling
                kmesh_full = kmesh_full * scaling
                kmesh_ired = kmesh_ired * scaling
            else:
                scaling = np.floor(ksampling / 2.0)
                kmesh_full = kmesh_full / scaling
                kmesh_ired = kmesh_ired / scaling
                if halfscale:
                    kmesh_full = 0.5 * kmesh_full
                    kmesh_ired = 0.5 * kmesh_ired

            # calculate ibz weights
            dummy, ibz_weights = np.unique(
                mapping_bz_to_ibz, return_counts=True)

        else:
            # here we only generate a full grid without fetching the IBZ or
            # related parameters
            logger.info(
                "Setting up the full k-point grid with a sampling "
                "of %sx%sx%s. No irreducible grid is determined.",
                str(ksampling[0]), str(ksampling[1]), str(ksampling[2]))
            if borderless:
                scaling = 1.0 / ksampling
                # in the future this needs to either be left or right adjusted
                # for non-gamma centered grids (right now this is not
                # supported)
                bzlimits = np.floor(ksampling / 2.0)
                k1 = np.linspace(-bzlimits[0], bzlimits[0], num=ksampling[0])
                k2 = np.linspace(-bzlimits[1], bzlimits[1], num=ksampling[1])
                k3 = np.linspace(-bzlimits[2], bzlimits[2], num=ksampling[2])
            else:
                logging.error(
                    "Inclusion of borders for the full grid method is"
                    "not supported. Exiting.")
                sys.exit(1)
            kx, ky, kz = np.array(np.meshgrid(k1, k2, k3, indexing='ij'))
            kmesh_full = np.array([kx.flatten(), ky.flatten(), kz.flatten()]).T
            # scale the grid
            kmesh_full = kmesh_full * scaling
            # set the IBZ and IBZ-BZ mapping to None as we now work on the full
            # grid
            kmesh_ired = None
            mapping_ibz_to_bz = None
            mapping_bz_to_ibz = None
            ibz_weights = None
        self.kmesh = kmesh_full
        self.kmesh_ired = kmesh_ired
        self.mapping_ibz_to_bz = np.unique(mapping_ibz_to_bz)
        self.mapping_bz_to_ibz = mapping_bz_to_ibz
        self.ksampling = ksampling
        self.ibz_weights = ibz_weights

    def regularcell(self):
        """
        Checks that all the vectors in the unit cell is orthogonal and thus if the cell is regular.

        Parameters
        ----------
        None

        Returns
        -------
        regular: boolean
            True if regular, False otherwise.

        """

        # set logger
        logger = logging.getLogger(sys._getframe().f_code.co_name)  # pylint: disable=protected-access
        logger.debug("Running regularcell.")

        regular = True

        # detect values in upper trigonal
        if self.unitcell[np.triu_indices(3, k=1)].any() > constants.zerocut:
            regular = False
        # detect values in lower trigonal
        if self.unitcell[np.tril_indices(3, k=-1)].any() > constants.zerocut:
            regular = False

        return regular

    def fetch_iksampling(self):
        """
        Fetches the a denser k-point sampling which is for instance used when one would like to interpolate

        Parameters
        ----------
        None

        Returns
        -------
        iksampling : ndarray
            | Dimension: (3)

            The number of requested k-point sampling along each
            reciprocal unit vector.

        """

        if np.all(self.param.dispersion_interpolate_sampling) == 0:
            iksampling = self.fetch_ksampling_from_stepsize(
                self.param.dispersion_interpolate_step_size)
        else:
            iksampling = np.array(self.param.dispersion_interpolate_sampling)

        return iksampling


def calculate_cell_volume(cell):
    """
    Calculates the cell volume.

    Parameters
    ----------
    cell : ndarray
        | Dimension: (3,3)

        Contains the i basis vectors of the cell, [i,:].

    Returns
    -------
    volume : float
        The volume of the cell in units of the units along
        the input axis cubed.

    """

    # set logger
    logger = logging.getLogger(sys._getframe().f_code.co_name)  # pylint: disable=protected-access
    logger.debug("Running calculate_cell_volume.")

    volume = np.abs(np.dot(cell[0], np.cross(cell[1], cell[2])))
    return volume


def check_sensible_ksampling(ksampling):
    """
    Check if the ksampling is sensible.

    Parameters
    ----------
    ksampling : ndarray
        | Dimension: (3)

        The k-point sampling along each reciprocal lattice vector.

    Returns
    -------
    None

    """

    # set logger
    logger = logging.getLogger(sys._getframe().f_code.co_name)  # pylint: disable=protected-access

    if np.any(ksampling) > 200:
        logger.error("A ksampling was detected above 200. This seems "
                     "extremely high. Please reconsider what is to be "
                     "done. Exiting.")
        sys.exit(1)
