"""Use the validation engine belonging to the running Home Assistant version."""

from importlib import import_module

from homeassistant.helpers import config_validation as cv

# HA 2026.11 switched from voluptuous to probatio. Either library may also be
# installed by another dependency, so availability cannot identify HA's engine.
Schema = type(cv.PLATFORM_SCHEMA)
validation = import_module(Schema.__module__.partition(".")[0])
