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
# python specifics
import sys
import math
import logging
import numpy as np
import xml.etree.cElementTree as ET
import scipy.interpolate
import copy
# locals
import inputoutput
import interface
import utils
import constants
import spglib_interface

class Bandstructure():
    """
    Handles the read in, generation and storrage of the
    bandstructure and its relevant parameters.

    Parameters
    ----------
    lattice : object
        A `Lattice()` object.
    param : object
        A `Param()` object.
    filename : string, optional
        Filename and relative path for the input file that is
        to be read.

    Notes
    -----
    The YAML general and bandstructure configuration (param.yml
    and bandparams.yml, respectively by default) files are read and
    the setup of the bandstructure is determined from these files.

    If an external band structure is supplied, i.e. from VASP
    (vasprun.xml is sufficient) the bandparams file is still needed as
    it contains parameters used to set up the scattering properties
    etc. at a later stage.

    Presently the following combination is possible:

    + Parametrized bands (parabolic, non-parabolic, Kane and
      a k^4 model)
    + Tight binding bands from PythTB, including more
      generalized Wannier functions
    + VASP input from the VASP XML file (only this file is needed)
    + Numpy input files

    A combination of parametrized and tight binding bands is possible.

    If another code is to be used to provide the bandstructure
    parameters, please consult :mod:`interface` and use that as a
    base to write a new `bandstructure_yourcode` function in that
    module and include a call to this function in the initializer below.

    .. todo:: Add the posibility to add parameterized bands
              to the e.g. VASP bandstructure. Usefull for instance to
              investigate defects etc. that was not included in the
              calculation.

    .. todo:: Add interfaces to other first principle codes.

    .. todo:: Add detailed documentation of the YAML configuration
              files.

    """

    def __init__(self, lattice, param, location=None, filename=None):
        # configure logger
        logger = logging.getLogger(sys._getframe().f_code.co_name)
        self.lattice = lattice
        self.param = param
        if self.param.read == "param":
            interface.bandstructure_param(self, location=location,
                                          filename=filename)
        elif self.param.read == "vasp":
            interface.bandstructure_vasp(self, location=location,
                                         filename=filename)
        elif ((self.param.read == "numpy")
              or (self.param.read == "numpyv")):
            interface.bandstructure_numpy(self, location=location,
                                          filename=filename)
        elif (self.param.read == "w90"):
            interface.bandstructure_w90(self, location=location,
                                        filename=filename)
        else:
            logging.error(
                "The supplied read parameter in general configuration "
                "file is not recognized. Exiting.")
            sys.exit(1)

        # scissor operator?
        if self.param.scissor:
            self.apply_scissor_operator()

    def locate_vbm(self, energies, occ):
        """
        Locate the valence band maximum.

        Parameters
        ----------
        energies : ndarray
            | Dimension: (N,M)

            The energy dispersion in eV for N bands at M k-points.
        occ : ndarray
            | Dimension: (N,M)

            The occupancy for N bands at M k-points.

        Returns
        -------
        energy : float
            The valence band maximum in eV
        band : int
            The band index of the valence band maximum.
        kpoint : int
            The kpoint index of the valence band maximum.
        """

        # set logger
        logger = logging.getLogger(sys._getframe().f_code.co_name)
        logger.debug("Running locate_vbm.")

        if energies is None:
            energies = self.energies

        if occ is None:
            occ = self.occ

        # fetch the band index for the vbm
        numkpoints = energies[0].shape[0]
        # ceil in case of partly occupied band (although then things
        # start to get messy)
        vbm_band = int(np.ceil(
            occ[(abs(occ) > self.param.occ_cutoff)].shape[0] / numkpoints)) - 1

        # fetch the kpoint for this band
        vbm_kpoint = np.argmax(energies[vbm_band])

        # return energy, band and kpoint index for the vbm
        return energies[vbm_band, vbm_kpoint], vbm_band, vbm_kpoint

    def locate_cbm(self, energies, occ):
        """
        Locate the conduction band minimum.

        Parameters
        ----------
        energies : ndarray
            | Dimension: (N,M)

            The energy dispersion in eV for N bands at M k-points.
        occ : ndarray
            | Dimension: (N,M)

            The occupancy for N bands at M k-points.

        Returns
        -------
        energy : float
            The conduction band minimum in eV.
        band : int
            The band index of the conduction band minimum.
        kpoint : int
            The kpoint index of the conduction band minimum.

        """

        # set logger
        logger = logging.getLogger(sys._getframe().f_code.co_name)
        logger.debug("Running locate_cbm.")

        if energies is None:
            energies = self.energies

        if occ is None:
            occ = self.occ

        # fetch the band index for the cbm
        numkpoints = energies[0].shape[0]
        # ceil in case of partly occupied band (although then things
        # start to get messy)
        cbm_band = occ.shape[0] - int(np.ceil(
            occ[(abs(occ) < self.param.occ_cutoff)].shape[0] /
            numkpoints))

        # fetch the kpoint for this band
        cbm_kpoint = np.argmin(energies[cbm_band])

        # return energy, band and kpoint index for the cbm
        return energies[cbm_band, cbm_kpoint], cbm_band, cbm_kpoint

    def locate_bandgap(self, energies=None, occ=None):
        """
        Locate the band gap.

        Parameters
        ----------
        energies : ndarray, optional
            | Dimension: (N,M)

            The energy dispersion in eV for N bands at M k-points.
            Defaults to the `energies` stored in the current
            `Bandstructure()` object.
        occ : ndarray, optional
            | Dimension: (N,M)

            The occupancy for N bands at M k-points. Defaults
            to the `occ` stored in the current `Bandstructure()` object.

        Returns
        -------
        vbm_energy : float
            The valence band maximum in eV.
        bandgap : float
            The band gap in eV.
        """
        # set logger
        logger = logging.getLogger(sys._getframe().f_code.co_name)
        logger.debug("Running locate_vbm_and_bandgap.")

        # fetch valence band maximum
        vbm_energy, vbm_band, vbm_kpoint = self.locate_vbm(
            energies=energies, occ=occ)
        # fetch conduction band minimum
        cbm_energy, cbm_band, cbm_kpoint = self.locate_cbm(
            energies=energies, occ=occ)

        # now we need to calculate the band gap
        band_gap = cbm_energy - vbm_energy
        # if band_gap is negative, we have either a semi-metal or metal,
        # so set band_gap to zero
        if band_gap < 0.0:
            band_gap = 0
        direct = True
        if vbm_kpoint != cbm_kpoint:
            direct = False

        return band_gap, direct

    def apply_scissor_operator(self, energies=None):
        """
        Apply scissor operator to blue or redshift the conduction band
        energies.

        Parameters
        ----------
        energies : ndarray, optional
            | Dimension: (N,M)

            The energy dispersion for N bands at M k-points. Defaults
            to the `energies` stored in the current `Bandstructure()`
            object.

        Returns
        -------
        None

        Notes
        -----
        .. warning:: Please beware that this is a rather brutal (but usefull)
                     operation. Make sure that the valence band maximum and
                     conduction band minimum is fetched correctly. One way to
                     check that this works is to plot the energies along a
                     representative direction before and after the scissor
                     operator have been executed.

        """
        # set logger
        logger = logging.getLogger(sys._getframe().f_code.co_name)
        logger.debug("Running apply_scissor_operator.")

        if energies is None:
            energies = self.energies

        if self.cbm_band is None:
            logger.error("The conduction band index `cbm_band` was "
                         "not found while trying to apply scissor "
                         "operator. Are you sure the dataset you "
                         "supplied contained enough detail to calculate "
                         "the valence band maximu, conduction band "
                         "minimum and band gap? Exiting.")
            sys.exit(1)

        if self.param.scissor:
            if self.metallic:
                logger.error(
                    "User requests to apply scissor operator to a "
                    "metallic system. Exiting.")
            logger.info(
                "Applying scissor operator and lifting the conduction "
                "bands")
            energies[self.cbm_band:] = energies[
                self.cbm_band:] + self.param.scissor

    def check_dos_energy_range(self, remin, remax):
        """
        Check that the energy grid of the density of states covers
        the range of the supplied paramters.

        Parameters
        ----------
        remin : float
            The energy in eV for the lowest requested energy.
        remax : float
            The energy in eV for the highest requested energy.

        Returns
        -------
        within : boolean
            True if the endpoints are within the energy range of the stored
            density of states, False ortherwise.

        """
        # set logger
        logger = logging.getLogger(sys._getframe().f_code.co_name)
        logger.debug("Running check_dos_energy_range.")

        within = True

        # dos exists?
        try:
            self.dos_energies
        except AttributeError:
            return False
        if self.dos_energies is None:
            return False

        if (remin < self.dos_energies[0]):
            logger.info("The lower limit on the energy of the sampled "
                        "density of states is not 'transport_energycutband' "
                        "away from the minimum of the requested chemical "
                        "potential.")
            within = False

        if (remax < self.dos_energies[self.dos_energies.shape[0] - 1]):
            logger.info("The high limit on the energy of the sampled "
                        "density of states is not 'transport_energycutband' "
                        "away from the maximum of the requested chemical "
                        "potential.")
            within = False

        return within

    def check_energyshifts(self):
        """
        Check the energy shift parameters in the parameter file for
        consistency.

        Parameters
        ----------
        None

        Returns
        -------
        None

        """
        # set logger
        logger = logging.getLogger(sys._getframe().f_code.co_name)
        logger.debug("Running apply_scissor_operator.")

        if (self.param.e_fermi_in_gap and
                self.param.e_fermi and
                self.param.e_vbm) or \
                (self.param.e_fermi_in_gap and
                 self.param.e_fermi) or \
                (self.param.e_fermi_in_gap and
                 self.param.e_vbm) or \
                (self.param.e_fermi and self.param.e_vbm):
            logger.error("User have sett more than one energy shift "
                         "parameter. Exiting.")
            sys.exit(1)

    def check_velocities(self, cutoff):
        """
        Check that there exists realistic values for the band
        velocities.

        Parameters
        ----------
        cutoff : float
            Cutoff value for the test in eVAA units.

        Returns
        -------
        boolean
            True if values are above `cutoff`, False otherwise.

        """
        # set logger
        logger = logging.getLogger(sys._getframe().f_code.co_name)
        logger.debug("Running check_velocities.")

        if (np.abs(self.velocities) < cutoff).all():
            logger.info("All band velocities are less than " +
                        str(cutoff) + ". Continuing.")
            return False

        return True

    def locate_band_gap(self):
        """"
        Calculate the band gap.

        Parameters
        ----------
        None

        Returns
        -------
        float
            The band gap in eV

        """

        # set logger
        logger = logging.getLogger(sys._getframe().f_code.co_name)
        logger.debug("Running locate_band_gap.")

        # loop bands
        band_gap = np.inf
        if self.bandparams.shape[0] < 2:
            logging.info(
                "Requesting band gap for one band system. Setting "
                "band gap to infinity and Fermi level to zero.")
            band_gap = np.inf
        for band in range(self.bandparams.shape[0]):
            # locate minimum value in all bands
            conduction = self.energies[band][
                np.where(self.energies[band] > constants.zerocut)]
            if conduction.shape[0] > 0:
                band_min = np.amin(self.energies[band][np.where(
                    self.energies[band] > constants.zerocut)])
                if band_min < band_gap:
                    band_gap = band_min
        return band_gap

    def tight_binding_energies(self, bandparam, location=None,
                               filename=None):
        """
        This routine sets up the interface to PythTB and execute
        the tight binding extractions.

        Parameters
        ----------
        bandparams : int
            The index of the tight binding band, which
            follows the sequential index of the bandstructure
            configuration file, which also
            need to be set for the respective band due to scattering
            mechanisms etc.

        Returns
        -------
            The tight binding energy dispersions in eV on the
            k-point grid storred in the current `Lattice()` object.

        Notes
        -----
        The tight binding parameters are set at the bottom of
        the bandstructure configuration file.

        Consult the documentation for
        `PythTB <http://www.physics.rutgers.edu/pythtb/>`_ for how
        to set these parameters.

        .. todo:: Extend the documentation of how to set parameters
                  relevant to PythTB.

        """
        # lazy import of PythTB
        import pythtb

        # set logger
        logger = logging.getLogger(sys._getframe().f_code.co_name)
        logger.debug("Running tight_binding_energies.")

        hop = self.tight_hop[bandparam]
        orb = self.tight_orb[bandparam]
        onsite = self.tight_onsite[bandparam]
        adjust_onsite = self.tight_adj_onsite[bandparam]
        lat = [self.lattice.unitcell[0].tolist(), self.lattice.unitcell[
            1].tolist(), self.lattice.unitcell[2].tolist()]
        # if orb is an empty list, set default to
        # atomic centered orbitals
        if not orb:
            orb = self.lattice.positions.tolist()
        tb = pythtb.tb_model(3, 3, lat, orb)
        tb.set_onsite(onsite)
        for jump in hop:
            tb.set_hop(jump[0], jump[1], jump[2], jump[3])
        if self.param.displaytb:
            tb.display()
        # make sure kmesh that we pass (in direct) goes from
        # zero to one, not -0.5 to 0.5.
        kmesh = self.lattice.kmesh + 0.5
        energies = tb.solve_all(kmesh)
        e_shape = energies.shape
        if e_shape[0] != len(orb):
            logger.error(
                "The number of bands returned from the solve_all "
                "function in PythTB does not match the number "
                "of entries of torb. Exiting.")
            sys.exit(1)
        velocities = np.zeros((e_shape[0], 3, e_shape[1]))
        # loop bands
        for band in range(e_shape[0]):
            if adjust_onsite:
                if adjust_onsite[band] == "max":
                    e_max = np.amax(energies[band])
                    energies[band] = energies[band] - e_max
                elif adjust_onsite[band] == "min":
                    e_min = np.amin(energies[band])
                    energies[band] = energies[band] - e_min
                else:
                    # we should really check what the user sets here...
                    energies[band] = energies[band] - adjust_onsite[band]

            # then make sure no values are truly zero (cause later
            # problems in scattering routines etc.)
            energies[band][np.abs(energies[band]) <
                           constants.zerocut] = constants.zerocut
        # PythTB does not return velocities...now go and fetch those using
        # interpolation
        logger.info(
            "PythTB does not presently return band velocities. "
            "Fetching those with an interpolation technique.")

        # this routine reside inside the analytic generation, so
        # gen_velocities are for sure set to False, set to True
        # so that the interpolate() routine can fetch the velocities
        self.gen_velocities = True
        # set energies temporarely (overwritten later)
        self.energies = energies
        kmesh = self.lattice.fetch_kmesh(direct=False)
        dummy, velocities, dummy = self.interpolate(
            kpoint_mesh=kmesh)
        # and then put back to False
        self.gen_velocities = False
        return energies, velocities

    def non_parabolic_energy_1(self, k, effmass, a, e0=0.0,
                               kshift=[0.0, 0.0, 0.0]):
        """
        Calculates a spherical energy dispersion, both parabolic
        and non-parabolic.

        Parameters
        ----------
        k : ndarray
            | Dimension: (N,3)

            Contains the N k-point coordinates (cartesian) where the
            dispersion is to be evaluated.
        effmass : ndarray
            | Dimension: (3)

            Contains the effective mass along the three k-point
            directions. Only the diagonal components of the effective
            mass tensor is used. In units of the free electron mass.
        a : ndarray
            | Dimension: (3)

            The non parabolic coefficients in front of the
            :math:`k^4` term.
        e0 : float, optional
            Shift of the energy scale in eV.
        kshift : ndarray, optional
            | Dimension: (3)

            The shift along the respective k-point vectors in
            cartesian coordinates.

        Returns
        -------
        ndarray
            | Dimension: (N)

            Contains the energy dispersion in eV at each N k-points.

        Notes
        -----
        This routines calculates the energy dispersion according to

        .. math:: E=\\frac{\\hbar^2 k^2}{2m}+ak^4.

        Setting :math:`a` to zero yields a spherical parabolic
        dispersion.

        """

        # set logger
        logger = logging.getLogger(sys._getframe().f_code.co_name)
        logger.debug("Running non_parabolic_energy_1.")

        k = k - kshift
        k2 = k * k
        k4 = k2 * k2
        k2 = np.sum(k2 / effmass, axis=1)
        k4 = np.sum(k4 * a, axis=1)
        return e0 + constants.bandunit * k2 + k4

    def non_parabolic_velocity_1(self, k, effmass, a,
                                 kshift=[0.0, 0.0, 0.0]):
        """
        Calculates the group velocity for the energy dispersion
        generated in :func:`non_parabolic_energy_1`, both parabolic
        and non-parabolic.

        Parameters
        ----------
        k : ndarray
            | Dimension: (N,3)

            Contains the N k-point in cartesian coordinates where
            the dispersion is to be evaluated.
        effmass : ndarray
            | Dimension: (3)

            Contains the effective mass along the three k-point
            directions. Only the diagonal components of the effective
            mass tensor is used. In units of the free electron mass.
        a : ndarray
            | Dimension: (3)

            The non parabolic coefficients in front of the
            :math:`k^4` term.
        kshift : ndarray, optional
            | Dimension: (3)

            The shift along the respective k-point vectors in
            cartesian coordinates.

        Returns
        -------
        vx, vy, vz : ndarray, ndarray, ndarray
            | Dimension: (N),(N),(N)

            Contains the group velocity at each N k-points
            for each direction defined by the direction of the k-point
            unit axis. Units of eVAA.

        Notes
        -----
        This routines calculates the group velocity according to

        .. math:: v=\\frac{\\partial E}{\\partial \\vec{k}},

        where

        .. math:: E=\\frac{\\hbar^2 k^2}{2m}+ak^4.

        Setting :math:`a` to zero yields a spherical parabolic dispersion
        and thus its group velocity.

        .. warning:: The factor :math:`\\hbar^{-1}` is not returned and
        need to be included externally.

        """

        # set logger
        logger = logging.getLogger(sys._getframe().f_code.co_name)
        logger.debug("Running non_parabolic_velocity_1.")

        k = k - kshift
        k2 = k * k
        k2 = np.sum(a * k2, axis=1)
        parabolic = np.divide(2.0 * constants.bandunit, effmass)
        scaling = (parabolic + 4.0 * np.column_stack((k2, k2, k2))).T
        spreadkz, spreadky, spreadkx = k.T[2], k.T[1], k.T[0]
        vx = np.multiply(scaling[0], spreadkx)
        vy = np.multiply(scaling[1], spreadky)
        vz = np.multiply(scaling[2], spreadkz)
        return vx, vy, vz

    def non_parabolic_energy_2(self, k, effmass, a):
        """
        Calculates a non-parabolic energy dispersion.

        Parameters
        ----------
        k : ndarray
            | Dimension: (N,3)

            Contains the N k-point cartesian coordinates where the
            dispersion is to be evaluated.
        effmass : float
            The effective mass in units of the free electron mass.
        a : float
            The :math:`\\alpha` factor.

        Returns
        -------
        ndarray
            | Dimension: (N)

            Contains the energy in eV at each N k-points for each
            direction defined by the direction of the k-point unit axis.

        Notes
        -----
        This routine calculates the energy dispersion according to

        .. math:: E(1+\\alpha E)=\\frac{\\hbar^2k^2}{2m},

        where :math:`\\alpha` is a parameter that adjust the
        non-parabolicity

        Note that if :math:`m` is negative (valence bands), the square
        root is undefined for :math:`k^2>=m/(2\\alpha \\hbar)`, which is
        a rather limited k-space volume. Consider (either :math:`m` or
        :math:`\\alpha` negative).

        .. math:: m=m_e, \\alpha=1.0 (E_g=1.0 \\mathrm{eV})
                  \\rightarrow |\\vec{k}| \\geq 0.26 \\mathrm{AA^{-1}}

        .. math:: m=0.1m_e, \\alpha=1.0 \\rightarrow |\\vec{k}|
                  \\geq 0.081 \\mathrm{AA^{-1}}

        .. math:: m=10m_e, \\alpha=1.0 \\rightarrow |\\vec{k}|
                  \\geq 0.81 \\mathrm{AA^{-1}}

        .. math:: m=m_e, \\alpha=10.0 (E_g=0.1 \\mathrm{eV})
                  \\rightarrow |\\vec{k}| \\geq 0.81 \\mathrm{AA^{-1}}

        .. math:: m=m_e, \\alpha=0.1 (E_g=10 \\mathrm{eV})
                  \\rightarrow |\\vec{k}| \\geq 0.081 \\mathrm{AA^{-1}}

        For a simple cell of 10 :math:`\\mathrm{AA}`, the BZ border is
        typically at 0.31 :math:`\\mathrm{AA^{-1}}` and for a smaller
        cell, e.g. 3.1 :math:`\\mathrm{AA}`, the BZ border is here
        at 1.0 :math:`\\mathrm{AA^{-1}}`.

        .. warning:: In order to be able to use all values of
                     :math:`a`, we return a linear :math:`E(\\vec{k})`
                     in the undefined region (the last defined value
                     of :math:`E(\\vec{k})` is used). This is highly
                     unphysical, so we print a warning to notice the user

        """

        # set logger
        logger = logging.getLogger(sys._getframe().f_code.co_name)
        logger.debug("Running non_parabolic_energy_2.")

        if (effmass < 0 and a > 0) or (effmass > 0 and a < 0):
            k2 = np.sum(k * k, axis=1)
            last_valid_k2 = -effmass / \
                (4 * a * constants.bandunit) - constants.zerocut
            last_valid_energy = (
                -1.0 + np.sqrt(1 + 4 * a *
                               (constants.bandunit * last_valid_k2 /
                                effmass))) / (2 * a)
            logging.warning("The product of the effective mass "
                            "and non parabolic correction factor "
                            "is negative. The Kane model is thus "
                            "only defined for a restricted k-vector set. "
                            "Returning a linear E(k)=E in the undefined "
                            "region, where the linear E is the last "
                            "allowed value. USER BEWARE THAT THE "
                            "TRANSPORT INTEGRALS COULD PICK UP THIS "
                            "DISCONTINUITY. Last valid energy for "
                            "the Kane model is " +
                            str(last_valid_energy) +
                            " eV. Turning of band folding.")
            # make sure we do not fold in this case
            self.param.band_folding = False
            energy = constants.bandunit * k2 / effmass
            # need to loop manually to fill the array outside the
            # valid range
            for i in range(k2.shape[0]):
                if k2[i] < last_valid_k2:
                    energy[i] = (-1.0 + np.sqrt(1 + 4 *
                                                a * energy[i])) / (2 * a)
                else:
                    energy[i] = last_valid_energy
            return energy
        else:
            e_parabolic = constants.bandunit * \
                np.sum(k * k, axis=1) / effmass
            return (-1.0 + np.sqrt(1 + 4 * a * e_parabolic)) / (2 * a)

    def non_parabolic_velocity_2(self, k, effmass, a):
        """
        Calculates the group velocity for the energy dispersion
        generated in :func:`non_parabolic_energy_2`, both parabolic
        and non-parabolic.

        Parameters
        ----------
        k : ndarray
            | Dimension: (N,3)

            Contains the N k-point in cartesian coordinates where
            the dispersion is to be evaluated.
        effmass : float
            The effective mass in units of the free electron mass.
        a : ndarray
            | Dimension: (3)

            The :math:`\\alpha` factor.

        Returns
        -------
        vx, vy, vz : ndarray, ndarray, ndarray
            | Dimension: (N), (N), (N)

            The group velocity along each axis in the reciprocal
            unit cell. In units of eVAA.

        Notes
        -----
        Consult comments in :func:`non_parabolic_energy_1`

        .. warning:: The factor :math:`\\hbar^{-1}` is not returned
                     and need to be included externally.

        """

        # set logger
        logger = logging.getLogger(sys._getframe().f_code.co_name)
        logger.debug("Running non_parabolic_velocity_2.")

        spreadkz, spreadky, spreadkx = k.T[2], k.T[1], k.T[0]
        # check validity
        if (effmass < 0 and a > 0) or (effmass > 0 and a < 0):
            k2 = np.sum(k * k, axis=1)
            last_valid_k2 = -effmass / \
                (4 * a * constants.bandunit)
            energy = constants.bandunit * k2 / effmass
            last_valid_energy = (-1.0 + np.sqrt(1 + 4 * a *
                                                (constants.bandunit *
                                                 last_valid_k2 /
                                                 effmass))) / (2 * a)
            # need to loop manually to fill the array outside the
            # valid range
            for i in range(k2.shape[0]):
                if k2[i] < last_valid_k2:
                    energy[i] = (-1.0 + np.sqrt(1 + 4 *
                                                a * energy[i])) / (2 * a)
                else:
                    energy[i] = last_valid_energy
        else:
            e_parabolic = constants.bandunit * \
                np.sum(k * k, axis=1) / effmass
            energy = (-1.0 + np.sqrt(1 + 4 * a * e_parabolic)) / (2 * a)
        scaling = 2.0 * constants.bandunit / (effmass *
                                              (1 + 2 * a * energy))
        vx = np.multiply(scaling, spreadkx)
        vy = np.multiply(scaling, spreadky)
        vz = np.multiply(scaling, spreadkz)
        return vx, vy, vz

    def gen_dos(self):
        """
        Generates the density of states for the analytic models

        Parameters
        ----------
        None

        Returns
        -------
        dos : ndarray
            | Dimension: (N, M)

            Contains the density of states for N bands at M
            `dos_num_samples` from `dos_e_min` to `dos_e_max`.
            The number of samplings and their range is set it
            general configuration file. Units are per volume
            unit, 1/eV/AA^3.
        dos_energies : ndarray
            | Dimension: (M)

            Contains the energy samplings of which `dos` was
            calculated in units of eV.

        Notes
        -----
        Currently only the parabolic/spherical models are
        implemented.

        .. todo:: Also implement the non-parabolic alpha models

        """
        # set logger
        logger = logging.getLogger(sys._getframe().f_code.co_name)
        logger.debug("Running gen_dos.")

        # now check if we have generated a TB band (or more), if so,
        # do not calculate dos. In the future, calculate dos for
        # the non-TB bands by analytick formulations and use a
        # numerical scheme for the TB bands
        if True in self.tb_band:
            return None, None

        # also check for any non parabolic character in the band
        # generation (alpha or k^4). Currently no analytic
        # expression is implemented to set these directly.
        # However, these do exist, so consider to add them in the
        # future.
        if np.any(self.bandparams[:, 1]) == 1 or \
           np.any(self.bandparams[:, 1]) == 2:
            return None, None

        dos_units = 0.1 * np.power(constants.elmass *
                                   constants.jtoev, 1.5) / \
            (np.sqrt(20) * np.power(constants.pi, 2.0) *
             np.power(constants.hbar, 3.0))

        numbands = self.effmass.shape[0]
        e0 = self.e0
        effmass = np.zeros(numbands)
        # use e_max for the dos limit
        dos_energies = np.linspace(self.param.dos_e_min,
                                   self.param.dos_e_max,
                                   self.param.dos_num_samples)
        dos = np.zeros((numbands, self.param.dos_num_samples))
        # some tests
        for band in range(numbands):
            # check spherical effmass for all bands
            if self.bandparams[band][0] != 0:
                logger.error("Band number " + str(band + 1) +
                             " is not parabolic and the user tries to "
                             "use closed expressions for the density of "
                             "states calculations. Exiting.")
                sys.exit(1)
            # check if the effective mass is indeed spherical
            self.spherical_effective_mass(self.effmass[band])
            effmass[band] = np.abs(self.effmass[band][0])
            energy_shift = dos_energies - e0[band]
            status = self.status[band]
            # conduction
            if status == "c":
                dos_entries = np.where(dos_energies - e0[band] > 0)
                dos[band, dos_entries] = dos[band, dos_entries] + \
                    self.spin_degen[band] * \
                    np.power(effmass[band], 1.5) * \
                    np.sqrt(energy_shift[dos_entries])

            # valence
            if status == "v":
                dos_entries = np.where(dos_energies - e0[band] < 0)
                dos[band, dos_entries] = dos[band, dos_entries] + \
                    self.spin_degen[band] * \
                    np.power(effmass[band], 1.5) * \
                    np.sqrt(np.abs(energy_shift[dos_entries]))
            # slam on the dos units
            dos = dos * dos_units
        return dos, dos_energies

    def gen_bands(self):
        """
        Generates the set of energy and velocity dispersions.

        Parameters
        ----------
        None

        Returns
        -------
        energies : ndarray
            | Dimension: (N,M)

            Contains the energy dispersions in eV for N bands
            at M k-points.
        velocities : ndarray
            | Dimension: (N,M,3)

            Contains the group velocities in eVAA of the energy
            dispersions (without the :math:`\\hbar^{-1}` factor).
        tb_band : ndarray
            | Dimension: (N)

            Contains boolean values of True for band indexes
            that are tight binding bands, False otherwise.

        """

        # set logger
        logger = logging.getLogger(sys._getframe().f_code.co_name)
        logger.debug("Running gen_bands.")

        # check if vasp is read, if so, only return
        if self.param.read == "vasp":
            return
        numbands = self.bandparams.shape[0]
        numkpoints = self.lattice.kmesh.shape[0]
        energies = np.zeros((numbands, numkpoints))
        velocities = np.zeros((numbands, 3, numkpoints))
        tb_band = []
        band = 0
        while band < numbands:
            # do everything but tight binding
            if self.bandparams[band][0] != 3:
                e, v = self.gen_analytic_band(band)
                energies[band] = e
                velocities[band] = v
                tb_band.append(False)
                band = band + 1
            else:
                # the tight binding generation can of course
                # generate more than one band, so we need to shift
                # everything due to this
                e, v = self.tight_binding_energies(band)
                numtbands = e.shape[0]
                energies[band:band + numtbands] = e[:]
                velocities[band:band + numtbands] = v[:]
                tb_band.extend([True] * numtbands)
                band = band + numtbands
        energies = np.array(energies, dtype="double")
        velocities = np.array(velocities, dtype="double")
        # check Fermi level
        if self.param.e_fermi_in_gap:
            # place Fermi level in the middle of the gap (if it exists,
            # otherwise set to zero)
            band_gap = self.locate_band_gap()
            if band_gap != np.inf:
                self.param.e_fermi = self.locate_band_gap() / 2.0
            else:
                logger.info(
                    "User wanted to place Fermi level in the middle "
                    "of the gap, but no gap was found. Setting Fermi "
                    "level to 0.0 eV. Continuing.")
                self.param.e_fermi = 0.0
        # return data
        return energies, velocities, tb_band

    def gen_analytic_band(self, band):
        """
        Generate an analytical energy and velocity dispersion.

        Parameters
        ----------
        band : int
            The band index used to fetch band parameters in
            bandparams.yml.

        Returns
        -------
        energy : ndarray
            | Dimension: (M)

            The energy dispersion in eV at M k-points.
        velocity : ndarray
            | Dimension: (M,3)

            The group velocity in eVAA of the energy dispersion (without
            the :math:`\\hbar^{-1}` factor).

        """

        # set logger
        logger = logging.getLogger(sys._getframe().f_code.co_name)
        logger.debug("Running gen_analytick_band.")

        # locate BZ border
        kdirmax = np.amax(self.lattice.kmesh, axis=0)
        kdirborder = np.array([[kdirmax[0], 0.0, 0.0],
                               [0.0, kdirmax[1], 0.0],
                               [0.0, 0.0, kdirmax[2]],
                               [kdirmax[0], kdirmax[1], 0.0],
                               [kdirmax[0], 0.0, kdirmax[2]],
                               [0.0, kdirmax[1], kdirmax[2]],
                               [kdirmax[0], kdirmax[1], kdirmax[2]]])
        kcartborder = self.lattice.dir_to_cart(kdirborder)
        # set generator functions
        band_function = self.bandparams[band][0]
        # set a to zero for parabolic bands
        if band_function == 0:
            self.a[band] = [0.0, 0.0, 0.0]
            generate_energy = self.non_parabolic_energy_1
            generate_velocity = self.non_parabolic_velocity_1
        else:
            if band_function == 1:
                generate_energy = self.non_parabolic_energy_1
                generate_velocity = self.non_parabolic_velocity_1
            elif band_function == 2:
                generate_energy = self.non_parabolic_energy_2
                generate_velocity = self.non_parabolic_velocity_2
            else:
                logging.error("Supplied non_parabolic_function=" +
                              str(band_function) + " does not exist")
                sys.exit(1)
        # fetch k-point grid in cartersian
        k = self.lattice.fetch_kmesh(direct=False)
        # do first band (no folding)
        # first energy
        energy = generate_energy(k, self.effmass[band], a=self.a[band],
                                 e0=self.e0[band], kshift=self.kshift[band])
        # then velocity
        vx, vy, vz = generate_velocity(k, self.effmass[band], a=self.a[band],
                                       kshift=self.kshift[band])
        # then do folding, if necessary
        # THIS DOES NOT WORK FOR MULTIBAND SCENARIOS, KILL IT
        # ACTUALLY, THIS FOLDING NEEDS TO BE FIXED IN ORDER TO GET THE
        # BAND INDEX FIRST DISABLE UNTIL FIXED!
        if self.bandparams[band][1] != 0:
            logging.error(
                "Band folding does not currently work due to band "
                "ordering. Exiting.")
            sys.exit(1)

        velocity = vx, vy, vz
        return np.array(energy), np.array(velocity)

    def fetch_velocities_along_line(self, kstart, kend, itype=None,
                                    itype_sub=None):
        """
        Calculate the velocity dispersion along a line in
        reciprocal space.

        Parameters
        ----------
        kstart : ndarray, optional
            | Dimension: (3)

            The start k - point in cartesian coordinates.
        kend : ndarray
            | Dimension: (3)

            The end k - point in cartesian coordinates.
        itype : string, optional
            | Can be any of:
            | {"linearnd", "interpn", "rbf", "einspline", "wildmagic", "skw"}

            The type of interpolate method to use. If not set, the parameter
            `dispersion_interpolate_method` in the general configuration
            file sets this.
        itype_sub : string, optional
            | Can be any of:
            | {"nearest", "linear"}, when `itype` is set to `interpn`.
            | {"multiquadric", "inverse_multiquadric", "gaussian", "linear",
            | "cubic", "quintic", "thin_plate"}, when `itype` is set to `rbf`.
            | {"natural", "flat", "periodic", "antiperiodic"}, when `itype`
            | is set to `einspline`.
            | {"trilinear, tricubic_exact, tricubic_bspline, akima"},
            | when `itype` is set to `wildmagic`.

            The subtype of the interpolation method.

        Returns
        -------
        velocities : ndarray
            | Dimension: (N, 3, M)

            The group velocity in units of eVAA along a line for N
            bands and M k - points, defined by the
            `num_kpoints_along_line` in the general configuration file.
        kpts : ndarray
            | Dimension: (M, 3)

            The kpoints where the group velocity was calculated.

        Notes
        -----
        The :func:`interpolate` is used to perform the interpolation
        of the data along the line.

        See Also
        --------
        interpolate

        .. warning:: The factor : math: `\\hbar^{-1}` is not returned
                      and need to be included externally.

        """
        # set logger
        logger = logging.getLogger(sys._getframe().f_code.co_name)
        logger.debug("Running fetch_velocities_along_line.")

        # now make sure the supplied points does not extend outside the
        # grid
        bz_border = self.lattice.fetch_bz_border(direct=True)
        utils.pull_vecs_inside_boundary(
            (kstart, kend), bz_border, constants.zerocut)

        kpts = self.lattice.fetch_kpoints_along_line(
            kstart, kend, self.param.num_kpoints_along_line, direct=False)
        velocities = self.fetch_velocities_at_kpoints(
            kpts, itype=itype, itype_sub=itype_sub)

        return velocities, kpts

    def fetch_velocities_at_kpoints(self, kpoint_mesh, itype=None,
                                    itype_sub=None):
        """
        Calculate the velocity dispersions at specific k - points.

        Parameters
        ----------
        kpoint_mesh : ndarray
            | Dimension: (N, 3)

            The k - point mesh for extraction in cartesian coordinates.
        itype : string, optional
            | Can be any of:
            | {"linearnd", "interpn", "rbf", "einspline", "wildmagic", "skw"}

            The type of interpolate method to use. If not set, the parameter
            `dispersion_interpolate_method` in the general configuration
            file sets this.
        itype_sub : string, optional
            | Can be any of:
            | {"nearest", "linear"}, when `itype` is set to `interpn`.
            | {"multiquadric", "inverse_multiquadric", "gaussian", "linear",
            | "cubic", "quintic", "thin_plate"}, when `itype` is set to `rbf`.
            | {"natural", "flat", "periodic", "antiperiodic"}, when `itype`
            | is set to `einspline`.
            | {"trilinear, tricubic_exact, tricubic_bspline, akima"},
            | when `itype` is set to `wildmagic`.

            The subtype of the interpolation method.

        Returns
        -------
        velocities : ndarray
            | Dimension: (N, 3, M)

            The group velocity in units of eVAA at the N bands for M
            k - points.

        Notes
        -----
        The :func:`interpolate` is used to perform the interpolation.

        See Also
        --------
        interpolate

        .. warning:: The factor : math: `\\hbar^{-1}` is not returned
                      and need to be included externally.

        """
        # set logger
        logger = logging.getLogger(sys._getframe().f_code.co_name)
        logger.debug("Running fetch_velocities_at_kpoints.")

        dummy, velocities, dummy = self.interpolate(
            kpoint_mesh=kpoint_mesh, itype=itype,
            itype_sub=itype_sub, ivelocities=True)
        return velocities

    def fetch_energies_along_line(self, kstart, kend, samplings=None,
                                  itype=None, itype_sub=None):
        """
        Calculate the energy dispersions along specific k - points.

        Parameters
        ----------
        kstart : ndarray
            | Dimension: (3)

            Direct k - point coordinate of the start point of line extraction.
        kend : ndarray
            | Dimension: (3)

            Direct k - point coordinate of the end point of line extraction.
        samplings : int, optional
            The number of N samples along the line. If not specified the
            variable is set to the the `num_kpoints_along_line`
            parameter in params.yml
        itype : string, optional
            | Can be any of:
            | {"linearnd", "interpn", "rbf", "einspline", "wildmagic", "skw"}

            The type of interpolate method to use. If not set, the parameter
            `dispersion_interpolate_method` in the general configuration
            file sets this.
        itype_sub : string, optional
            | Can be any of:
            | {"nearest", "linear"}, when `itype` is set to `interpn`.
            | {"multiquadric", "inverse_multiquadric", "gaussian", "linear",
            | "cubic", "quintic", "thin_plate"}, when `itype` is set to `rbf`.
            | {"natural", "flat", "periodic", "antiperiodic"}, when `itype`
            | is set to `einspline`.
            | {"trilinear, tricubic_exact, tricubic_bspline, akima"},
            | when `itype` is set to `wildmagic`.

            The subtype of the interpolation method.

        Returns
        -------
        energies : ndarray
            | Dimension: (N, M)

            The energy dispersions in eV along a line for N bands and M
            k - points, defined by the `num_kpoints_along_line`
            in the general configuration file.
        kpts : ndarray
            | Dimension: (N, 3)

            The k - point mesh for the line extraction in cartesian
            coordinates.

        Notes
        -----
        The routine :func:`interpolate` is used to perform the
        interpolation of the data along the line.

        See Also
        --------
        interpolate

        """

        # set logger
        logger = logging.getLogger(sys._getframe().f_code.co_name)
        logger.debug("Running fetch_energies_along_line.")

        # now make sure the supplied points does not extend outside the
        # grid
        bz_border = self.lattice.fetch_bz_border(direct=True)
        utils.pull_vecs_inside_boundary(
            (kstart, kend), bz_border, constants.zerocut)

        kpts = self.lattice.fetch_kpoints_along_line(
            kstart, kend, self.param.num_kpoints_along_line,
            direct=False)
        energies = self.fetch_energies_at_kpoints(
            kpoint_mesh=kpts, itype=itype, itype_sub=itype_sub)

        return energies, kpts

    def fetch_energies_at_kpoints(self, kpoint_mesh, itype=None,
                                  itype_sub=None):
        """
        Calculate the energy dispersions at specific
        k - points by interpolation.

        Parameters
        ----------
        kpoint_mesh : ndarray
            | Dimension: (N, 3)

            The N k - point coordinates in cartesian coordinates.
        itype : string, optional
            | Can be any of:
            | {"linearnd", "interpn", "rbf", "einspline", "wildmagic", "skw"}

            The type of interpolate method to use. If not set, the parameter
            `dispersion_interpolate_method` in the general configuration
            file sets this.
        itype_sub : string, optional
            | Can be any of:
            | {"nearest", "linear"}, when `itype` is set to `interpn`.
            | {"multiquadric", "inverse_multiquadric", "gaussian", "linear",
            | "cubic", "quintic", "thin_plate"}, when `itype` is set to `rbf`.
            | {"natural", "flat", "periodic", "antiperiodic"}, when `itype`
            | is set to `einspline`.
            | {"trilinear, tricubic_exact, tricubic_bspline, akima"},
            | when `itype` is set to `wildmagic`.

            The subtype of the interpolation method.

        Returns
        -------
        energies : ndarray
            | Dimension: (N, M)

            The energies in eV for each of the N bands and M k - points.

        Notes
        -----
        The routine :func:`interpolate` is used to perform the
        interpolation.

        See Also
        --------
        interpolate

        """

        # set logger
        logger = logging.getLogger(sys._getframe().f_code.co_name)
        logger.debug("Running fetch_energies_at_kpoints.")

        # fetch points
        energies, dummy, dummy = self.interpolate(kpoint_mesh=kpoint_mesh,
                                                  itype=itype,
                                                  itype_sub=itype_sub)
        return energies

    def interpolate(self, iksampling=None, ienergies=True,
                    ivelocities=False, itype=None, itype_sub=None,
                    kpoint_mesh=None, store_inter=False):
        """
        Interpolates the energies and velocity dispersion.

        Parameters
        ----------
        iksampling : ndarray, optional
            | Dimension: (3)

            Contains the interpolated k - point mesh sampling values.
            Does not have to be set if line extraction performed
            (full grid is instead supplied in line_mesh).
        ienergies : boolean
            If True, interpolate the energies, if not, do not.
        ivelocities : boolean
            If True, interpolate the velocities, if not, do not
        itype : string, optional
            Can be any of:
            {"linearnd", "interpn", "rbf", "einspline", "wildmagic", "skw"}
            The type of interpolate method to use. If not set, the parameter
            `dispersion_interpolate_method` in the general configuration file
            sets this.
        itype_sub : string, optional
            Can be any of:
            {"nearest", "linear"}, when `itype` is set to `interpn`.
            {"multiquadric", "inverse_multiquadric", "gaussian", "linear",
            "cubic", "quintic", "thin_plate"}, when `itype` is set to `rbf`.
            {"natural", "flat", "periodic", "antiperiodic"}, when `itype`
            is set to `einspline`.
            {"trilinear, tricubic_exact, tricubic_bspline, akima"},
            when `itype` is set to `wildmagic`.
            The subtype of the interpolation method.
        kpoint_mesh : ndarray, optional
            | Dimension: (M, 3)

            Supplied k - point grid for extraction as an alternative
            to iksampling. Should be supplied in cartesian coordinates
            and should not extend the border of the original grid.
        store_inter : boolean, optional
            Store the new interpolated energies and velocities in
            the supplied object. Also modifies the current `Lattice()` object
            with the new grid etc. if that has been modified.
            Defaults to False.

        Returns
        -------
        ien, ivel1, ivel2, ivel3 : ndarray, ndarray, ndarray, ndarray
            | Dimension: (M), (M, 3), (M, 3), (M, 3)

            The energy dispersions in eV, and group velocities in eVAA
            along indexes each axis of the reciprocal basis are returned
            for M new k - points if velocities is supplied, or if the
            `gen_velocities` tag is set to True in the current
            `Bandstructure()` object.
        ien, False, False, False : ndarray, boolean, boolean, boolean
            | Dimension: (M)

            The energy dispersions in eV for the M new k - points if
            `velocities` is not supplied or `gen_velocities` is
            set to False.

        See Also
        --------
        linearnd
        interpn
        rbf

        .. todo:: DOCUMENT THE DIFFERENT INTERPOLATION SCHEMES,
                  OR AT LEAST ADD PROPER REFERENCES.

        """

        # set logger
        logger = logging.getLogger(sys._getframe().f_code.co_name)
        logger.debug("Running interpolate.")

        if ienergies:
            try:
                energies = self.energies
            except AttributeError:
                logger.error(
                    "The energies is not stored in the current "
                    "Bandstructure() object. Exiting.")
                sys.exit(1)

        if ivelocities:
            try:
                velocities = self.velocities
            except AttributeError:
                logger.error(
                    "The band velocities is not stored in the current "
                    "Bandstructure() object. Exiting.")
                sys.exit(1)

        if iksampling is None:
            iksampling = np.array(self.param.dispersion_interpolate_sampling)

        # set interpolate method from param file, if not supplied
        # also demand that is itype or itype_sub is supplied, the other also
        # has to be supplied
        needs_sub = {"interpn", "rbf", "einspline", "wildmagic"}
        if (itype is None) and (itype_sub is None):
            itype = self.param.dispersion_interpolate_method
            itype_sub = self.param.dispersion_interpolate_type
        else:
            if itype in needs_sub and itype_sub is None:
                logger.error(
                    "If supplying itype, also supply the relevant "
                    "itype_sub flag and vice versa. Exiting.")
                sys.exit(1)
            if itype is None:
                logger.error(
                    "The itype parameter is not given upon calling "
                    "the interpolation routine. Exiting.")
                sys.exit(1)

        # now check for valid itype, itype_sub is done later
        possible_itypes = {"linearnd", "interpn",
                           "rbf", "einspline", "wildmagic", "skw"}
        if itype not in possible_itypes:
            logger.error(
                "The specified itype (or dispersion_interpolate_method "
                "in the general configuraiton file) is not recognized. "
                "Exiting.")
            sys.exit(1)

        gen_velocities = self.gen_velocities
        if gen_velocities and ivelocities:
            if not ((itype == "einspline") or (itype == "wildmagic")):
                logger.error("Gradient extraction for the band "
                             "velocities are only supported for the "
                             "einspline/wildmagic_akima/"
                             "wildmagic_tricubic_bspline/"
                             "wildmagic_tricubic_exact/"
                             "wildmagic_trilinear interpolation method. "
                             "Please change and rerun. Exiting.")
                sys.exit(1)

        # set ksampling
        ksampling = self.lattice.ksampling

        # for these intepolation routines, it is best to work on the
        # full BZ for the moment
        num_kpoints_energy = self.energies.shape[1]
        if num_kpoints_energy != self.lattice.kmesh.shape[0]:
            if num_kpoints_energy == self.lattice.kmesh_ired.shape[0]:
                # SKW need IBZ grid, so this is fine
                if itype != "skw":
                    logger.debug(
                        "Irreducible data detected in interpolation "
                        "routine. Laying out the full BZ before continuing "
                        "with the interpolation routines")
                    energies = energies[:, self.lattice.mapping_bz_to_ibz]
                    velocities = velocities[
                        :, :, self.lattice.mapping_bz_to_ibz]
            else:
                logger.error(
                    "The number of k-points for the energies does not "
                    "match the full BZ or the IBZ grid. Exiting.")
                sys.exit(1)
        else:
            # if SKW, we need to reduce the grid to the IBZ
            if itype == "skw":
                energies = energies[:, self.lattice.mapping_ibz_to_bz]

        # check that gamma_center mode is enabled:
        if not self.param.gamma_center:
            logger.error(
                "The automatic interpolation routines only work if "
                "gamma_center is set to True. Either turn of interpolation "
                "or switch to gamma_center. Exiting.")
            sys.exit(1)

        # fetch old grid in cartesian (IBZ in direct for SKW)
        if itype != "skw":
            old_grid = self.lattice.fetch_kmesh(direct=False)
        else:
            old_grid = self.lattice.fetch_kmesh(direct=True, ired=True)

        ksampling_old = ksampling

        # set new grid, special case for SKW, then the grid is created in the
        # SKW routine (Fourier transformation, so no need to create a new one)
        # also, we need to make sure the interpolator grid does not go out of
        # bounds as most methods cannot handle extrapolation (by increasing
        # their grid point sampling one extends towards BZ edge and thus need to
        # extrapolate)
        # one easy way to do this is to modify the basis such that it goes from
        # +/- the original grids endpoints (as these seldom fall at the BZ edge).
        #
        # calculate correction vector used to rescale the reciprocal unitcell
        # and obtain a new real unitcell that is passed to spglib for grid
        # generation, in this way the endpoints of the original point is
        # included
        # WE ONLY DO THIS IF kpoint_mesh IS None
        if kpoint_mesh is None:
            logger.info("The kpoint mesh have been adjusted in the "
                        "interpolation routines and sqeezed into the "
                        "original bz zone to avoid extrapolation issues. "
                        "Be carefull when modifying this grid in the future.")
            lattice_old = copy.deepcopy(self.lattice)
            direct_width_bz_old = (ksampling - 1) / (ksampling.astype(
                dtype="double"))
            direct_width_bz_new = (iksampling - 1) / (iksampling.astype(
                dtype="double"))
            dvec = direct_width_bz_old / direct_width_bz_new
            # we also have to be sure that the grid does not extend its
            # border (most routines does not support extrapolation)
            dvec = (1 - 1e-6) * dvec
            if itype != "skw":
                # store old bz border to be used in the interpolation
                # routines below
                old_bz_border = self.lattice.fetch_bz_border(direct=False)
                self.lattice.create_kmesh_spg(iksampling)
                # now this is the crucial part, scale the grid by
                # dvec to pull it inside the borders
                self.lattice.kmesh = self.lattice.kmesh * dvec
                self.lattice.kmesh_ired = self.lattice.kmesh_ired * dvec
                # make sure the grid is in cartesian coordinates
                new_grid = self.lattice.fetch_kmesh(direct=False)
            else:
                new_grid = old_grid
        else:
            new_grid = kpoint_mesh

        num_new_kpoints = new_grid.shape[0]
        num_bands = energies.shape[0]
        ien = np.zeros((num_bands, num_new_kpoints), dtype=np.double)
        if gen_velocities or ivelocities:
            ivel1 = np.zeros((num_bands, num_new_kpoints), dtype=np.double)
            ivel2 = np.zeros((num_bands, num_new_kpoints), dtype=np.double)
            ivel3 = np.zeros((num_bands, num_new_kpoints), dtype=np.double)
        # loop bands for python stuff, otherwise do loop of bands inside C
        # routines
        if itype == "linearnd" or itype == "interpn" or itype == "rbf":
            for band in range(num_bands):
                ien_band = None
                ivel1_band = None
                ivel2_band = None
                ivel3_band = None
                if itype == "linearnd":
                    logger.info(
                        "Interpolating using Scipy LinearNDInterpolator.")
                    intere = scipy.interpolate.LinearNDInterpolator(
                        old_grid, energies[band])
                    ien_band = intere(new_grid)
                    if ivelocities:
                        intervel1 = scipy.interpolate.LinearNDInterpolator(
                            old_grid, velocities[band][0])
                        intervel2 = scipy.interpolate.LinearNDInterpolator(
                            old_grid, velocities[band][1])
                        intervel3 = scipy.interpolate.LinearNDInterpolator(
                            old_grid, velocities[band][2])
                        ivel1_band = intervel1(new_grid)
                        ivel2_band = intervel2(new_grid)
                        ivel3_band = intervel3(new_grid)
                if itype == "interpn":
                    logger.info("Interpolating using Scipy interpn.")
                    old_grid_unit_vecs = self.lattice.fetch_kmesh_unit_vecs(
                        direct=False)
                    kxp, kyp, kzp = old_grid_unit_vecs
                    eshape = energies[band].reshape(
                        ksampling_old[0], ksampling_old[1], ksampling_old[2])
                    possible_methods = ["nearest", "linear"]
                    if itype_sub not in possible_methods:
                        logger.error("The specified itype_sub is not recognized "
                                     "by the interpn method, please consult the "
                                     "Scipy documentation for valid flags. "
                                     "Possible flags for itype_sub are: " +
                                     ', '.join(map(str, possible_methods)) +
                                     ". Exiting.")
                        sys.exit(1)
                    ien_band = scipy.interpolate.interpn(
                        (kxp, kyp, kzp), eshape, new_grid, method=itype_sub)
                    if ivelocities:
                        vel1shape = velocities[band][0].reshape(
                            ksampling_old[0], ksampling_old[1], ksampling_old[2])
                        vel2shape = velocities[band][1].reshape(
                            ksampling_old[0], ksampling_old[1], ksampling_old[2])
                        vel3shape = velocities[band][2].reshape(
                            ksampling_old[0], ksampling_old[1], ksampling_old[2])
                        ivel1_band = scipy.interpolate.interpn(
                            (kxp, kyp, kzp), vel1shape, new_grid, method=itype_sub)
                        ivel2_band = scipy.interpolate.interpn(
                            (kxp, kyp, kzp), vel2shape, new_grid, method=itype_sub)
                        ivel3_band = scipy.interpolate.interpn(
                            (kxp, kyp, kzp), vel3shape, new_grid, method=itype_sub)
                if itype == "rbf":
                    logger.info("Interpolating using Scipy Rbf.")
                    # this RBF routine uses crazy amounts of memory
                    kx_old, ky_old, kz_old = old_grid.T
                    kx_new, ky_new, kz_new = new_grid.T
                    if itype_sub not in constants.rbf_functions:
                        logger.error("The specified itype_sub is not recognized "
                                     "by the Rbf method, please consult the Scipy "
                                     "documentation for valid flags. Possible flags "
                                     "for itype_sub are: " +
                                     ', '.join(map(str, constants.rbf_functions)) +
                                     ". Exiting.")
                        sys.exit(1)
                    # force Rbf to go through the original points
                    smooth = 0.0
                    intere = scipy.interpolate.Rbf(kx_old, ky_old, kz_old,
                                                   energies[band],
                                                   function=itype_sub,
                                                   smooth=selfmooth)
                    ien_band = intere(kx_new, ky_new, kz_new)
                    if ivelocities:
                        intervel1 = scipy.interpolate.Rbf(kx_old, ky_old, kz_old,
                                                          velocities[band][0],
                                                          function=itype_sub,
                                                          smooth=smooth)
                        ivel1_band = intervel1(kx_new, ky_new, kz_new)
                        intervel2 = scipy.interpolate.Rbf(kx_old, ky_old, kz_old,
                                                          velocities[band][1],
                                                          function=itype_sub,
                                                          smooth=smooth)
                        ivel2_band = intervel2(kx_new, ky_new, kz_new)
                        intervel3 = scipy.interpolate.Rbf(kx_old, ky_old, kz_old,
                                                          velocities[band][2],
                                                          function=itype_sub,
                                                          smooth=smooth)
                        ivel3_band = intervel3(kx_new, ky_new, kz_new)

                ien[band] = ien_band
                if ivelocities:
                    ivel1[band] = ivel1_band
                    ivel2[band] = ivel2_band
                    ivel3[band] = ivel3_band

        # loop over bands in the C code
        if itype == "einspline":
            logger.info("Interpolating using Einspline.")
            # lazy import of einspline (optional)
            import einspline

            # set boundary conditions
            bz_border = old_bz_border
            domainx = np.array(
                [bz_border[0], bz_border[1]], dtype=np.double)
            domainy = np.array(
                [bz_border[2], bz_border[3]], dtype=np.double)
            domainz = np.array(
                [bz_border[4], bz_border[5]], dtype=np.double)
            ix = np.ascontiguousarray(new_grid.T[0], dtype=np.double)
            iy = np.ascontiguousarray(new_grid.T[1], dtype=np.double)
            iz = np.ascontiguousarray(new_grid.T[2], dtype=np.double)
            if itype_sub not in constants.einspline_boundary_cond:
                logger.error("The specified itype_sub is not recognized "
                             "by the einspline method, please consult the "
                             "einspline documentation for valid flags. "
                             "Possible flags for itype_sub are: " +
                             ', '.join(map(str, constants.einspline_boundary_cond)) +
                             ". Notice that the DERIV1 and DERIV2 flags are "
                             "not available in this version of the "
                             "interface. Exiting.")
                sys.exit(1)

            if gen_velocities:
                einspline.einspline_execute_interface(ksampling_old,
                                                      domainx,
                                                      domainy,
                                                      domainz,
                                                      np.ascontiguousarray(
                                                          energies, dtype="double"),
                                                      ix, iy, iz, ien,
                                                      ivel1, ivel2, ivel3, 1,
                                                      itype_sub)
            else:
                # no velocities, just create some dummies of length one
                dummy = np.zeros((num_bands, 1), dtype=np.double)
                einspline.einspline_execute_interface(ksampling_old,
                                                      domainx,
                                                      domainy,
                                                      domainz,
                                                      np.ascontiguousarray(
                                                          energies, dtype="double"),
                                                      ix, iy, iz, ien,
                                                      dummy, dummy, dummy, 0,
                                                      itype_sub)
            if ivelocities and not gen_velocities:
                # create a dummy
                dummy = np.zeros((num_bands, 1), dtype=np.double)
                einspline.einspline_execute_interface(
                    ksampling_old,
                    domainx, domainy, domainz,
                    np.ascontiguousarray(velocities[:, 0, :],
                                         dtype=np.double),
                    ix, iy, iz, ivel1,
                    dummy, dummy, dummy,
                    0, itype_sub)
                einspline.einspline_execute_interface(
                    ksampling_old,
                    domainx, domainy, domainz,
                    np.ascontiguousarray(velocities[:, 1, :],
                                         dtype=np.double),
                    ix, iy, iz, ivel2,
                    dummy, dummy, dummy,
                    0, itype_sub)
                einspline.einspline_execute_interface(
                    ksampling_old,
                    domainx, domainy, domainz,
                    np.ascontiguousarray(velocities[:, 2, :],
                                         dtype=np.double),
                    ix, iy, iz, ivel3,
                    dummy, dummy, dummy,
                    0, itype_sub)

        if itype == "wildmagic":
            if itype_sub not in constants.wildmagic_methods:
                logger.error("The specified itype_sub is not recognized by "
                             "the Wildmagic method, please consult the "
                             "Wildmagic documentation for valid flags. "
                             "Possible flags for itype_sub are: " +
                             ', '.join(map(str, constants.wildmagic_methods)) +
                             ". Exiting.")
                sys.exit(1)
            logger.info("Interpolating using Wildmagic/GeometricTools.")
            # lazy import of wildmagic (optional)
            import wildmagic

            # set boundary conditions
            bz_border = old_bz_border
            domainx = np.ascontiguousarray(
                np.array([bz_border[0], bz_border[1]], dtype=np.double))
            domainy = np.ascontiguousarray(
                np.array([bz_border[2], bz_border[3]], dtype=np.double))
            domainz = np.ascontiguousarray(
                np.array([bz_border[4], bz_border[5]], dtype=np.double))
            new_grid_trans = new_grid.T
            ix = np.ascontiguousarray(new_grid_trans[0], dtype="double")
            iy = np.ascontiguousarray(new_grid_trans[1], dtype="double")
            iz = np.ascontiguousarray(new_grid_trans[2], dtype="double")
            np.set_printoptions(threshold=np.nan)
            if itype_sub == "trilinear":
                if gen_velocities:
                    wildmagic.trilinear_gradient_execute_interface(
                        ksampling_old, domainx, domainy, domainz,
                        np.ascontiguousarray(
                            energies, dtype="double"), ix, iy, iz, ien,
                        ivel1, ivel2, ivel3)
                else:
                    wildmagic.trilinear_execute_interface(
                        ksampling_old, domainx, domainy, domainz,
                        np.ascontiguousarray(energies, dtype="double"),
                        ix, iy, iz, ien)
            elif itype_sub == "tricubic_exact":
                if gen_velocities:
                    wildmagic.tricubic_exact_gradient_execute_interface(
                        ksampling_old, domainx, domainy, domainz,
                        np.ascontiguousarray(
                            energies, dtype="double"), ix, iy, iz, ien,
                        ivel1, ivel2, ivel3)
                else:
                    wildmagic.tricubic_exact_execute_interface(
                        ksampling_old, domainx, domainy, domainz,
                        np.ascontiguousarray(energies, dtype="double"),
                        ix, iy, iz, ien)
            elif itype_sub == "tricubic_bspline":
                if gen_velocities:
                    wildmagic.tricubic_bspline_gradient_execute_interface(
                        ksampling_old, domainx, domainy, domainz,
                        np.ascontiguousarray(
                            energies, dtype="double"), ix, iy, iz, ien,
                        ivel1, ivel2, ivel3)
                else:
                    wildmagic.tricubic_bspline_execute_interface(
                        ksampling_old, domainx, domainy, domainz,
                        np.ascontiguousarray(energies, dtype="double"),
                        ix, iy, iz, ien)
            elif itype_sub == "akima":
                if gen_velocities:
                    wildmagic.akima_gradient_execute_interface(
                        ksampling_old, domainx, domainy, domainz,
                        np.ascontiguousarray(
                            energies, dtype="double"), ix, iy, iz, ien,
                        ivel1, ivel2, ivel3)
                else:
                    wildmagic.akima_execute_interface(
                        ksampling_old, domainx, domainy, domainz,
                        np.ascontiguousarray(energies, dtype="double"),
                        ix, iy, iz, ien)
            else:
                logger.error("The specified itype_sub is not recognized "
                             "by the wildmagic method, please consult the "
                             "wildmagic documentation for valid flags. "
                             "Possible flags for itype_sub are: "
                             "trilinear, tricubic_exact, tricubic_bspline "
                             "and akima. Exiting.")
                sys.exit(1)

            if ivelocities and not gen_velocities:
                if itype_sub == "trilinear":
                    wildmagic.trilinear_execute_interface(
                        ksampling_old, domainx, domainy, domainz,
                        np.ascontiguousarray(
                            velocities[:, 0, :], dtype=np.double),
                        ix, iy, iz, ivel1)
                    wildmagic.trilinear_execute_interface(
                        ksampling_old,
                        domainx, domainy, domainz,
                        np.ascontiguousarray(
                            velocities[:, 1, :], dtype=np.double),
                        ix, iy, iz, ivel2)
                    wildmagic.trilinear_execute_interface(
                        ksampling_old,
                        domainx, domainy, domainz,
                        np.ascontiguousarray(
                            velocities[:, 2, :], dtype=np.double),
                        ix, iy, iz, ivel3)

                elif itype_sub == "tricubic_exact":
                    wildmagic.tricubic_exact_execute_interface(
                        ksampling_old,
                        domainx, domainy, domainz,
                        np.ascontiguousarray(velocities[:, 0, :],
                                             dtype=np.double),
                        ix, iy, iz, ivel1)
                    wildmagic.tricubic_exact_execute_interface(
                        ksampling_old,
                        domainx, domainy, domainz,
                        np.ascontiguousarray(velocities[:, 1, :],
                                             dtype=np.double),
                        ix, iy, iz, ivel2)
                    wildmagic.tricubic_exact_execute_interface(
                        ksampling_old,
                        domainx, domainy, domainz,
                        np.ascontiguousarray(velocities[:, 2, :],
                                             dtype=np.double),
                        ix, iy, iz, ivel3)

                elif itype_sub == "tricubic_bspline":
                    wildmagic.tricubic_bspline_execute_interface(
                        ksampling_old,
                        domainx, domainy, domainz,
                        np.ascontiguousarray(velocities[:, 0, :],
                                             dtype=np.double),
                        ix, iy, iz, ivel1)
                    wildmagic.tricubic_bspline_execute_interface(
                        ksampling_old,
                        domainx, domainy, domainz,
                        np.ascontiguousarray(velocities[:, 1, :],
                                             dtype=np.double),
                        ix, iy, iz, ivel2)
                    wildmagic.tricubic_bspline_execute_interface(
                        ksampling_old,
                        domainx, domainy, domainz,
                        np.ascontiguousarray(velocities[:, 2, :],
                                             dtype=np.double),
                        ix, iy, iz, ivel3)

                elif itype_sub == "akima":
                    wildmagic.akima_execute_interface(
                        ksampling_old,
                        domainx, domainy, domainz,
                        np.ascontiguousarray(velocities[:, 0, :],
                                             dtype=np.double),
                        ix, iy, iz, ivel1)
                    wildmagic.akima_execute_interface(
                        ksampling_old,
                        domainx, domainy, domainz,
                        np.ascontiguousarray(velocities[:, 1, :],
                                             dtype=np.double),
                        ix, iy, iz, ivel2)
                    wildmagic.akima_execute_interface(
                        ksampling_old,
                        domainx, domainy, domainz,
                        np.ascontiguousarray(velocities[:, 2, :],
                                             dtype=np.double),
                        ix, iy, iz, ivel3)
                else:
                    logger.error("The specified itype_sub is not recognized "
                                 "by the wildmagic method, please consult "
                                 "the wildmagic documentation for valid "
                                 "flags. Possible flags for itype_sub are: "
                                 "trilinear, tricubic_exact, tricubic_bspline "
                                 "and akima. Exiting.")
                    sys.exit(1)

        if itype == "skw":
            # lazy import of skw_interface (optional)
            import skw_interface

            logger.info("Interpolating using SKW.")
            unitcell = self.lattice.unitcell
            positions = self.lattice.positions
            species = self.lattice.species
            factor = self.param.skw_expansion_factor
            ksampling = self.lattice.ksampling
            # SKW works with the IBZ
            # controlling SKW grid is difficult, so just eat it
            new_grid, ien, ivel, iksampling = skw_interface.\
                interpolate(np.ascontiguousarray(energies, dtype="double"),
                            np.ascontiguousarray(
                    old_grid, dtype="double"),
                    np.ascontiguousarray(
                    ksampling, dtype="intc"),
                    np.ascontiguousarray(
                    unitcell, dtype="double"),
                    np.ascontiguousarray(
                    positions, dtype="double"),
                    np.ascontiguousarray(species, dtype="intc"),
                    factor)
        else:
            if gen_velocities or ivelocities:
                # squeeze all individual directions into one array
                ivel = np.zeros((ivel1.shape[0], 3, ivel1.shape[1]))
                ivel[:, 0, :] = ivel1
                ivel[:, 1, :] = ivel2
                ivel[:, 2, :] = ivel3

        # store in current object
        if store_inter:
            if iksampling is None:
                logging.info(
                    "It is not possible to store the new interpolation "
                    "grid in the Lattice() object when iksampling is "
                    "not supplied. User beware that the grid and the "
                    "data for the energies and velocities might now "
                    "not be compatible. The grid data for the supplied "
                    "kpoint_mesh is not stored. Continuing.")
            # the new grid is already in direct coordinates, but need
            # the IBZ grid. SKW creates borderless kpoint meshes
            if itype == "skw":
                self.lattice.create_kmesh_spg(iksampling, borderless=True)
                np.seterr(divide='ignore', invalid='ignore')
                if not np.all(np.nan_to_num((self.lattice.kmesh - new_grid)
                                            / new_grid) < self.param.symprec):
                    logger.error(
                        "The regenerated kpoint mesh from SPGLIB "
                        "does not match the output from the SKW "
                        "routines within symprec defined in params.yml. "
                        "Exiting.")
                    sys.exit(1)

            self.energies = ien
            if ivelocities or gen_velocities:
                self.velocities = ivel
            else:
                self.velocities = None
        # only return
        else:
            # reset lattice
            if kpoint_mesh is None:
                self.lattice = lattice_old
            if ivelocities or gen_velocities:
                return ien, ivel, new_grid
            else:
                return ien, False, new_grid

    def spherical_effective_mass(self, effmass_t):
        """
        Checks if the supplied effective mass array is spherical.

        Parameters
        ----------
        effmass_t : ndarray
            The effective mass tensor in units of the free
            electron mass.

        Returns
        -------
        boolean
            True if spherical tensors, False otherwise.

        """

        # set logger
        logger = logging.getLogger(sys._getframe().f_code.co_name)
        logger.debug("Running spherical_effective_mass.")

        effmass = effmass_t[0]
        if not np.allclose(np.array([effmass, effmass, effmass]),
                           effmass_t, atol=constants.zerocut):
            logger.error(
                "This routine requires a spherical effective mass "
                "tensor. Exiting.")
            sys.exit(1)
        return True

    def calc_density_of_states(self, spin_degen=False,
                               return_data=False, num_samples=None,
                               auto_scale=False, transport=False,
                               integral_method=None, interpol_method=None,
                               interpol_type=None):
        """ Calculate the density of states.

        Parameters
        ----------
        spin_degen : boolean, optional
            If True, include spin degeneracy (basically a factor of 2 in
            the DOS amplitude).
        return_data : boolean, optional
            If True, return the density of states data instead of
            storing it in the current `Bandstructure()` object.  If False, set
            `dos_energies`, `dos_partial` and `dos_total` in the current
            `Bandstructure()` object.
        num_samples : integers, optional
            Number of energy samples. Necessary if auto_scale
            is set. Otherwise the `dos_num_samples` in the parameter file
            is used.
        auto_scale : boolean, optional
            If True, auto scale the energy axis to cover the supplied
            band structure. Otherwise the `dos_e_min` and `dos_e_max` from
            the parameter file is used to set up the energy range unless
            `transport` is set which overrides this.
        transport : bool, optional
            Set to True if the density of states calculated are to be
            used in transport calculations (i.e. to set up the scattering).
            This ensures that the energy range covers the requested
            range of chemical potential pluss / minus
            'transport_energycutband' and is then padded with zeros
            for the remaining values down and up to the minimum and
            maximum values present in the `energies` entry of the
            `Bandstructure()` object. Using this option make it possible
            to calculate the density of states on a fine grid in the
            relevant transport region.
        integral_method : string, optional
            The integration method used to calculate the DOS:

            | "trapz" trapeziodal integration, uses :func:`scipy.integrate.trapz`
            | "simps" simpson integration, uses :func:`scipy.integrate.simps`
            | "romb" romberb integration, uses :func:`scipy.integrate.romb`
            | "tetra" tetrahedron method, uses the linear tetrahedron method
                      implemented in
                      `Spglib <https://atztogo.github.io/spglib/>`_
                      (up to version 1.7.4) by A. Togo.
            | "cubature" cubature method, using the
                         `Cubature <http://ab-initio.mit.edu/wiki/index.php/Cubature>`_
                         packages written by Steven G. Johnson.

            If not supplied, set according to `dos_integrating_method` in
            the general configuration file.
        interpol_method : string, optional
            The interpolation method used for the cubature method: (all
            methods from the Wildmagic / GeometricTools package). Now, only
            the option "wildmagic" is supported.
            If not supplied, set according to dos_interpolate_method in
            the general configuration file.
        interpol_type : string, optional
            The interpolation method used for the cubature method (all
            methods rely on the `GeometricTools <https://www.geometrictools.com/>`_ package):

            | "akima" `Akima <https://www.geometrictools.com/Documentation/AkimaInterpolation.pdf>`_
                      interpolation method
            | "trilinear" trilinear interpolation method
            | "tricubic_exact" exact analytic tricubic interpolation method
            | "tricubic_bspline" bspline tricubic interpolation method

            If not supplied, set according to `dos_interpolate_method` in
            the general configuration file.

        Returns
        -------
        dos_energies : ndarray
            | Dimension: (N)

            Array containing the density of states energies, where N is
            num_samples or set by the sampling determined in the
            general configuration file if `auto_scale` is set to False.
        dos_total : ndarray
            | Dimension: (N)

            Array containing the total density of states per volume unit (units
            1 / eV / AA ^ 3) at N sampled energy points, where N is determined
            from dos_energies.
        dos_partial : ndarray
            | Dimension: (M, N)

            Array containing the partial (for each band index M) density
            of states per volume unit (1 / eV / AA ^ 3) at N sampled energy
            points, where N is determined from dos_energies.

        See Also
        --------
        scipy.integrate.trapz
        scipy.integrate.simps
        scipy.integrate.romb

        """

        # set logger
        logger = logging.getLogger(sys._getframe().f_code.co_name)
        logger.debug("Running calc_density_of_states.")

        # set some constants
        num_bands = self.energies.shape[0]

        # volume of reciprocal unit cell
        volume_bz = np.linalg.det(self.lattice.runitcell)
        # volume of the unit cell
        volume = np.linalg.det(self.lattice.unitcell)

        # check if ibz grid is not None
        if self.lattice.kmesh_ired is None:
            logger.error("The irreducible grid is not available. "
                         "This can for instance happen if the user have "
                         "preinterpolated the grid using a interpolation "
                         "routine where determining the IBZ is difficult. "
                         "Exiting.")
            sys.exit(1)
        else:
            num_kpoints_ibz = self.lattice.kmesh_ired.shape[0]
        if integral_method is None:
            integral_method = self.param.dos_integrating_method
        if interpol_method is None:
            interpol_method = self.param.dos_interpolate_method
        if interpol_type is None:
            interpol_type = self.param.dos_interpolate_type
        # now, determine if the dos is to be used for transport
        if transport:
            logger.info("Adjusting the density of states energy "
                        "interval to "
                        "[transport_chempo_min-transport_energycutband:"
                        "transport_chempot_max+transport_energycutband]")
            e_min = (self.param.transport_chempot_min -
                     self.param.transport_energycutband)
            e_max = (self.param.transport_chempot_max +
                     self.param.transport_energycutband)
            dos_energies = self.fetch_dos_energies(e_min=e_min, e_max=e_max)
        else:
            dos_energies = self.fetch_dos_energies(auto_scale=auto_scale)
        num_samples = dos_energies.shape[0]
        dos = np.zeros((num_bands, num_samples), dtype='double')
        int_dos = np.zeros((num_bands, num_samples), dtype='double')
        if integral_method == 'tetra' or integral_method == 'smeared':
            if integral_method == 'tetra':
                weight_type = 0
                smearing = 0.0
                logger.debug("Running tetrahedron method.")
                if self.param.read == "numpy":
                    logger.error(
                        "The tetrahedron method is not supported when "
                        "reading data from Numpy yet. Exiting.")
                    sys.exit(1)
            else:
                weight_type = 2
                smearing = self.param.dos_smearing
                logger.debug("Running smearing method.")
            # here we call the weighted DOS routine (accepts IBZ
            # data)
            energies_ibz = np.take(
                self.energies, self.lattice.mapping_ibz_to_bz, axis=1)
            spglib_interface.calc_density_of_states_interface(
                energies_ibz, self.lattice.spg_kmesh,
                np.ascontiguousarray(
                    self.lattice.mapping_bz_to_ibz, dtype='intc'),
                np.ascontiguousarray(
                    self.lattice.mapping_ibz_to_bz, dtype='intc'),
                np.ascontiguousarray(self.lattice.ibz_weights, dtype='intc'),
                self.lattice.ksampling, self.lattice.runitcell,
                dos_energies, num_samples, num_bands, num_kpoints_ibz,
                self.spin_degen, volume, volume_bz, weight_type,
                smearing, dos, int_dos)
        elif integral_method == 'cubature':
            logger.debug("Running cubature method.")
            # lazy import of cubature_wildmagic (optional)
            import cubature_wildmagic

            # here we call the cubature DOS routine with on the fly
            # interpolation
            num_points = np.ascontiguousarray(self.lattice.ksampling,
                                              dtype=np.intc)
            # set boundary conditions (luckily we work in direct
            # coordinates)
            bz_border = self.lattice.fetch_bz_border(direct=False)
            domainx = np.ascontiguousarray(
                np.array([bz_border[0], bz_border[1]], dtype=np.double))
            domainy = np.ascontiguousarray(
                np.array([bz_border[2], bz_border[3]], dtype=np.double))
            domainz = np.ascontiguousarray(
                np.array([bz_border[4], bz_border[5]], dtype=np.double))
            max_it = self.param.cubature_max_it
            abs_err = self.param.cubature_abs_err
            rel_err = self.param.cubature_rel_err
            h = self.param.cubature_h
            # somethimes we do not want to print library output
            info = self.param.libinfo
            if interpol_method == "wildmagic":
                if interpol_type not in constants.wildmagic_methods:
                    logger.error("The specified interpol_type is not "
                                 "recognized by the Wildmagic method, "
                                 "please consult the Wildmagic documentation "
                                 "for valid flags. Possible flags for "
                                 "itype_sub are: " +
                                 ', '.join(map(str, constants.wildmagic_methods)) +
                                 ". Exiting.")
                    sys.exit(1)
                if interpol_type == "akima":
                    cubature_wildmagic.calc_density_of_states_interface(
                        num_points,
                        domainx, domainy, domainz,
                        self.energies,
                        dos_energies,
                        self.spin_degen,
                        num_samples, num_bands,
                        self.param.dos_smearing,
                        3,
                        self.param.dos_smear_then_interpolate,
                        volume, volume_bz,
                        max_it, abs_err, rel_err, h,
                        info,
                        dos, int_dos)
                elif interpol_method == "trilinear":
                    cubature_wildmagic.calc_density_of_states_interface(
                        num_points,
                        domainx, domainy, domainz,
                        self.energies,
                        dos_energies,
                        self.spin_degen,
                        num_samples,
                        num_bands,
                        self.param.dos_smearing,
                        0,
                        self.param.dos_smear_then_interpolate,
                        volume, volume_bz,
                        max_it, abs_err, rel_err, h,
                        info,
                        dos, int_dos)
                elif interpol_method == "tricubic_exact":
                    cubature_wildmagic.calc_density_of_states_interface(
                        num_points,
                        domainx, domainy, domainz,
                        self.energies,
                        dos_energies,
                        self.spin_degen,
                        num_samples, num_bands,
                        self.param.dos_smearing,
                        1,
                        self.param.dos_smear_then_interpolate,
                        volume, volume_bz,
                        max_it, abs_err, rel_err, h,
                        info,
                        dos, int_dos)
                elif interpol_method == "tricubic_bspline":
                    cubature_wildmagic.calc_density_of_states_interface(
                        num_points,
                        domainx, domainy, domainz,
                        self.energies,
                        dos_energies,
                        self.spin_degen,
                        num_samples, num_bands,
                        self.param.dos_smearing,
                        2,
                        self.param.dos_smear_then_interpolate,
                        volume, volume_bz,
                        max_it, abs_err, rel_err, h,
                        info,
                        dos, int_dos)
            else:
                logger.error(
                    "Currently, only interpol_method of 'wildmagic' is "
                    "supported when executing the cubature interpolation "
                    "routines. Also remember to set interpol_type accordingly. "
                    "Exiting.")
                sys.exit(1)
        elif integral_method == "trapz" or integral_method == "simps" or \
                integral_method == "romb":
            logger.debug(
                "Running trapeziodal, simpson or romberg integration.")
            kx, ky, kz = self.lattice.fetch_kmesh_unit_vecs(direct=False)
            # assume regular grid
            kx = kx[1] - kx[0]
            ky = ky[1] - ky[0]
            kz = kz[1] - kz[0]
            # now if we want romberg, we need to check for add grid samples
            # also make sure that kx, ky, kz is a float and the space between
            # each point for romberb (only trapz and simps support array)
            if integral_method == "romb":
                if not utils.is_power_of_two(self.lattice.ksampling[0] - 1):
                    logger.error("User requests romberg integration, but "
                                 "the samplings in the first direction is not "
                                 "2^k - 1. Exiting.")
                    sys.exit(1)
                if not utils.is_power_of_two(self.lattice.ksampling[1] - 1):
                    logger.error("User requests romberg integration, but "
                                 "the samplings in the second direction is not "
                                 "2^k - 1. Exiting.")
                    sys.exit(1)
                if not utils.is_power_of_two(self.lattice.ksampling[2] - 1):
                    logger.error("User requests romberg integration, but "
                                 "the samplings in the third direction is not "
                                 "2^k - 1. Exiting.")
                    sys.exit(1)
            for band in range(0, num_bands):
                spin_factor = self.spin_degen[band]
                energies_shaped = self.energies[band].reshape(
                    self.lattice.ksampling[0],
                    self.lattice.ksampling[1],
                    self.lattice.ksampling[2])
                for sample_index, sample_energy in np.ndenumerate(dos_energies):
                    energies_smeared = self.gaussian(energies_shaped,
                                                     sample_energy,
                                                     self.param.dos_smearing)
                    if integral_method == "trapz":
                        dos[band][sample_index] = spin_factor * \
                            scipy.integrate.trapz(
                                scipy.integrate.trapz(
                                    scipy.integrate.trapz(
                                        energies_smeared, dx=kz), dx=ky), dx=kx) / \
                            volume_bz / volume
                    elif integral_method == "simps":
                        dos[band][sample_index] = spin_factor * \
                            scipy.integrate.simps(
                                scipy.integrate.simps(
                                    scipy.integrate.simps(
                                        energies_smeared, dx=kz), dx=ky), dx=kx) / \
                            volume_bz / volume
                    elif integral_method == "romb":
                        dos[band][sample_index] = spin_factor * \
                            scipy.integrate.romb(
                                scipy.integrate.romb(
                                    scipy.integrate.romb(
                                        energies_smeared, dx=kz),
                                    dx=ky), dx=kx) / \
                            volume_bz / volume

        else:
            logger.error(
                "The supplied integral_method is not supported. "
                "Use: 'tetra', 'cubature','trapz','simps' or 'romb'. "
                "Exiting.")
            sys.exit(1)
        dos_total = dos.sum(-2)
        if not return_data:
            self.dos_partial = dos
            self.dos = dos_total
            self.dos_energies = dos_energies
        else:
            return dos_energies, dos_total, dos

    def fetch_dos_energies(self, e_min=None, e_max=None, num_samples=None,
                           auto_scale=False):
        """ Set up the energy array for density of states calculations.

        Parameters
        ----------
        e_min : float
            The mininum energy in eV.
        e_max : float
            The maximum energy in eV.
        num_samples : integer
            The N number of samples between `e_min` and `e_max`.
        auto_scale : boolean
            If True, set the energy scale from the supplied band
            structure, otherwise set the scale according to `e_min` and
            `e_max`.

        Returns
        -------
        ndarray
            | Dimension: (N)

            The N energies in eV where density of states calculations
            are to be performed.

        """

        # set logger
        logger = logging.getLogger(sys._getframe().f_code.co_name)
        logger.debug("Running fetch_dos_energies.")

        if num_samples is None:
            num_samples = self.param.dos_num_samples

        if (e_min or e_max) is None:
            if auto_scale:
                # fetch maximum and minimum energy from the
                # bandstructure and pad with 0.1 eV in order to catch
                # the DOS head and tail
                e_min = np.amin(self.energies) - 0.1
                e_max = np.amax(self.energies) + 0.1
            else:
                e_min = self.param.dos_e_min
                e_max = self.param.dos_e_max
        return np.linspace(e_min, e_max, num_samples)

    def fetch_min_max_energy(self):
        """ Returns the min and max of the energy in the current
        `Bandstructure()` object.

        Parameters
        ----------
        None

        Returns
        -------
        emin : float
            The minimum energy in eV located in the current `Bandstructure()`
            object.
        emax : float
            The maximum energy in eV located in the current `Bandstructure()`
            object.

        """

        # set logger
        logger = logging.getLogger(sys._getframe().f_code.co_name)
        logger.debug("Running fetch_min_max_energy.")

        emin = np.amin(self.energies)
        emax = np.amax(self.energies)

        return emin, emax

    def gaussian(self, energy, energy_ref, smearing):
        """ Returns the value of a Gaussian function.

        Parameters
        ----------
        energy : float
            The energy in eV.
        energy_ref : float
            The reference energy in eV.
        smearing : float
            The smearing factor in eV.

        Returns
        -------
        float
            The value in eV.

        """
        energy_shifted = energy_ref - energy
        return np.exp(-0.5 * np.power(energy_shifted / smearing, 2.0)) / \
            (smearing * np.sqrt(2 * np.pi))