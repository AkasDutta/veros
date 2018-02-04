import os
import pkg_resources
import multiprocessing.managers

import numpy as np
np.random.seed(17)

from veros import VerosLegacy, variables
from veros.timer import Timer


class VerosLegacyDummy(VerosLegacy):
    def set_parameter(self):
        pass

    def set_grid(self):
        pass

    def set_topography(self):
        pass

    def set_diagnostics(self):
        pass

    def after_timestep(self):
        pass

    def set_coriolis(self):
        pass

    def set_initial_conditions(self):
        pass

    def set_forcing(self):
        pass

    def set_fortran_attribute(self, attribute, value):
        import numpy as np
        for module_handle in self.modules:
            if hasattr(module_handle, attribute):
                try:
                    v = np.asfortranarray(value.copy2numpy())
                except AttributeError:
                    v = np.asfortranarray(value)
                setattr(module_handle, attribute, v)
                break
        else:
            raise AttributeError("Legacy pyOM has no attribute {}".format(attribute))

    def get_fortran_attribute(self, attribute):
        for module_handle in self.modules:
            if hasattr(module_handle, attribute):
                return getattr(module_handle, attribute)
        else:
            raise AttributeError("Legacy pyOM has no attribute {}".format(attribute))

    def call_fortran_routine(self, routine, *args, **kwargs):
        routine_handle = getattr(self.fortran, routine)
        return routine_handle(*args, **kwargs)


class VerosUnitTest(object):
    legacy_modules = ("main_module", "isoneutral_module", "tke_module",
                      "eke_module", "idemix_module")
    array_attributes = pkg_resources.resource_string("veros", "data/array_attributes").strip().split("\n")
    scalar_attributes = pkg_resources.resource_string("veros", "data/scalar_attributes").strip().split("\n")
    extra_settings = None
    test_module = None
    test_routines = None

    def __init__(self, dims=None, fortran=None):
        self.veros_new = VerosLegacyDummy()
        self.veros_new.pyom_compatibility_mode = True

        fortran = fortran or os.environ.get("PYOM2_LIB")
        if not fortran:
            raise RuntimeError("Path to fortran library must be given via keyword argument "
                               "or as environment variable PYOM2_LIB")

        class VerosLegacyManager(multiprocessing.managers.BaseManager):
            pass

        VerosLegacyManager.register("VerosLegacyDummy", VerosLegacyDummy)

        manager = VerosLegacyManager()
        manager.start()
        self.veros_legacy = manager.VerosLegacyDummy(fortran=fortran)

        if dims:
            self.nx, self.ny, self.nz = dims

        self.set_attribute("nx", self.nx)
        self.set_attribute("ny", self.ny)
        self.set_attribute("nz", self.nz)

        if self.extra_settings:
            for attribute, value in self.extra_settings.items():
                self.set_attribute(attribute, value)

        self.veros_new.set_legacy_parameter()
        variables.allocate_variables(self.veros_new)

        self.veros_legacy.call_fortran_routine("my_mpi_init", 0)
        self.veros_legacy.call_fortran_routine("pe_decomposition")
        self.veros_legacy.set_legacy_parameter()
        self.veros_legacy.call_fortran_routine("allocate_main_module")
        self.veros_legacy.call_fortran_routine("allocate_isoneutral_module")
        self.veros_legacy.call_fortran_routine("allocate_tke_module")
        self.veros_legacy.call_fortran_routine("allocate_eke_module")
        self.veros_legacy.call_fortran_routine("allocate_idemix_module")

    def set_attribute(self, attribute, value):
        if isinstance(value, np.ndarray):
            getattr(self.veros_new, attribute)[...] = value
        else:
            setattr(self.veros_new, attribute, value)

        self.veros_legacy.set_fortran_attribute(attribute, value)

    def get_attribute(self, attribute):
        try:
            veros_attr = getattr(self.veros_new, attribute)
        except AttributeError:
            veros_attr = None
        try:
            veros_attr = veros_attr.copy2numpy()
        except AttributeError:
            pass

        try:
            veros_legacy_attr = self.veros_legacy.get_fortran_attribute(attribute)
        except AttributeError:
            veros_legacy_attr = None

        return veros_attr, veros_legacy_attr

    def get_all_attributes(self, attributes):
        return {a: v for a, v in zip(attributes, map(self.get_attribute, attributes)) if all(vi is not None for vi in v)}

    def check_scalar_objects(self):
        differing_objects = {}
        scalars = self.get_all_attributes(self.scalar_attributes)
        for s, (v1, v2) in scalars.items():
            if ((v1 is None) != (v2 is None)) or v1 != v2:
                differing_objects[s] = (v1, v2)
        return differing_objects

    def check_array_objects(self):
        differing_objects = {}
        arrays = self.get_all_attributes(self.array_attributes)
        for a, (v1, v2) in arrays.items():
            if ((v1 is None) != (v2 is None)) or not np.array_equal(v1, v2):
                differing_objects[a] = (v1, v2)
        return differing_objects

    def initialize(self):
        raise NotImplementedError("Must be implemented by test subclass")

    def _normalize(self, *arrays):
        if any(a.size == 0 for a in arrays):
            return arrays
        norm = np.abs(arrays[0]).max()
        if norm == 0.:
            return arrays
        return (a / norm for a in arrays)

    def check_variable(self, var, atol=1e-8, data=None):
        if data is None:
            v1, v2 = self.get_attribute(var)
        else:
            v1, v2 = data
        if v1 is None or v2 is None:
            print("Variable {} is None".format(var))
            return False
        if v1.ndim > 1:
            v1 = v1[2:-2, 2:-2, ...]
        if v2.ndim > 1:
            v2 = v2[2:-2, 2:-2, ...]
        np.testing.assert_allclose(*self._normalize(v1, v2), atol=atol)
        return True

    def run(self):
        self.initialize()
        differing_scalars = self.check_scalar_objects()
        differing_arrays = self.check_array_objects()
        if differing_scalars or differing_arrays:
            print("The following attributes do not match between old and new veros after initialization:")
            for s, (v1, v2) in differing_scalars.items():
                print("{}, {}, {}".format(s, v1, v2))
            for a, (v1, v2) in differing_arrays.items():
                if np.asarray(v1).size == 0:
                    print("{}, {!r}, {!r}".format(a, None, np.max(v2)))
                elif np.asarray(v2).size == 0:
                    print("{}, {!r}, {!r}".format(a, np.max(v1), None))
                else:
                    print("{}, {!r}, {!r}".format(a, np.max(v1), np.max(v2)))

        veros_timers = {k: Timer("veros " + k) for k in self.test_routines}
        veros_legacy_timers = {k: Timer("veros legacy " + k) for k in self.test_routines}

        for routine in self.test_routines.keys():
            veros_args, veros_legacy_args = self.test_routines[routine]
            with veros_timers[routine]:
                getattr(self.test_module, routine)(*veros_args)
            veros_timers[routine].print_time()
            with veros_legacy_timers[routine]:
                self.veros_legacy.call_fortran_routine(routine, **veros_legacy_args)
            veros_legacy_timers[routine].print_time()
            self.test_passed(routine)
            self.initialize()


class VerosRunTest(VerosUnitTest):
    Testclass = None
    timesteps = None
    extra_settings = None

    def __init__(self, **kwargs):
        self.fortran = kwargs.get("fortran") or os.environ.get("PYOM2_LIB")
        if not self.fortran:
            raise RuntimeError("Path to fortran library must be given via keyword argument or command line")

        for attr in ("Testclass", "timesteps"):
            if getattr(self, attr) is None:
                raise AttributeError("attribute '{}' must be set".format(attr))

    def run(self):
        self.veros_new = self.Testclass()
        self.veros_new.setup()

        class VerosLegacyManager(multiprocessing.managers.BaseManager):
            pass

        VerosLegacyManager.register("Testclass", self.Testclass)

        manager = VerosLegacyManager()
        manager.start()
        self.veros_legacy = manager.Testclass(fortran=self.fortran)
        self.veros_legacy.setup()

        if self.extra_settings:
            for key, val in self.extra_settings.items():
                self.set_attribute(key, val)

        # integrate for some time steps and compare
        if self.timesteps > 0:
            self.veros_new.runlen = self.timesteps * self.veros_new.dt_tracer
            self.veros_new.run()

            self.veros_legacy.set_fortran_attribute("runlen", self.timesteps * self.veros_new.dt_tracer)
            self.veros_legacy.run()

        return self.test_passed()
