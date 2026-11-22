"""A fake PLC for your test suite: one YAML file, OPC UA and Modbus TCP."""

from .config import ConfigError, PLCConfig, load_config
from .expr import ExpressionError
from .plc import FakePLC
from .tags import UnknownTagError, Write

__all__ = [
    "ConfigError",
    "ExpressionError",
    "FakePLC",
    "PLCConfig",
    "UnknownTagError",
    "Write",
    "load_config",
]

__version__ = "0.1.0"
