# How to create modules  {#make-modules}

This page explains the normal Qudi module pattern. For a beginner-friendly overview, also read `documentation/student_guide/architecture_and_runtime.md`.

## Decide on the structure

Before writing code, decide which layer your feature belongs to:

| Need | Create |
| --- | --- |
| Talk to a real instrument or dummy instrument | Hardware module in `hardware/` |
| Define a contract that several hardware modules can implement | Interface in `interface/` |
| Coordinate a measurement, analysis, fitting, or saving workflow | Logic module in `logic/` |
| Present controls and plots to a user | GUI module in `gui/` |
| Adapt or combine modules without changing either side | Interfuse in `logic/interfuse/` |

The intended dependency direction is:

```text
GUI -> Logic -> Hardware
```

Do not put hardware-specific serial ports, IP addresses, NI channels, or lab paths directly in logic or GUI code. Use `ConfigOption` and the config file.

## Creating an interface

Create an interface when more than one module should be usable through the same contract.

```python
from abc import abstractmethod
from core.meta import InterfaceMetaclass


class ExampleCounterInterface(metaclass=InterfaceMetaclass):
    @abstractmethod
    def get_count_rate(self):
        """Return the current count rate in counts per second."""
        pass
```

Guidelines:

- Keep interfaces small and device-neutral.
- Include units in method names, docstrings, or returned metadata.
- Add capability or constraint methods when the GUI needs ranges, channels, or supported modes.
- Avoid names tied to one vendor or one experiment.

## Creating a hardware module

Hardware modules inherit from `core.module.Base` and usually implement one or more interfaces.

```python
from core.module import Base
from core.configoption import ConfigOption
from interface.slow_counter_interface import SlowCounterInterface


class MyCounter(Base, SlowCounterInterface):
    _resource = ConfigOption('resource', missing='error')
    _timeout = ConfigOption('timeout', default=5, missing='warn')

    def on_activate(self):
        self.log.info('Open device using configured resource %s', self._resource)
        self._device = None

    def on_deactivate(self):
        self._device = None

    def get_counter_channels(self):
        return ['count']

    def get_constraints(self):
        return {}
```

Config example:

```yaml
hardware:
    my_counter:
        module.Class: 'my_counter.MyCounter'
        resource: 'Dev1/ctr0'
        timeout: 5
```

Guidelines:

- Put all lab-specific values in `ConfigOption`.
- Validate unsafe values before sending them to hardware.
- Implement `on_deactivate()` so the device ends in a safe state.
- Prefer dummy hardware for development and tests.

## Creating a logic module

Logic modules usually inherit from `logic.generic_logic.GenericLogic`. They use `Connector` to access hardware or other logic modules.

```python
from qtpy import QtCore
from core.connector import Connector
from core.statusvariable import StatusVar
from logic.generic_logic import GenericLogic


class MyCounterLogic(GenericLogic):
    counter = Connector(interface='SlowCounterInterface')
    count_frequency = StatusVar('count_frequency', default=10)

    sigCountChanged = QtCore.Signal(float)

    def on_activate(self):
        self._counter = self.counter()

    def on_deactivate(self):
        self._counter = None

    def read_once(self):
        value = self._counter.get_counter()[0]
        self.sigCountChanged.emit(value)
```

Config example:

```yaml
logic:
    my_counter_logic:
        module.Class: 'my_counter_logic.MyCounterLogic'
        connect:
            counter: 'my_counter'
```

Guidelines:

- Logic should not import a concrete hardware class.
- Use signals to notify GUI modules.
- Use `StatusVar` for user/runtime state that should survive restart.
- Keep analysis and device IO separable enough to test with dummy hardware.

## Creating a GUI module

GUI modules inherit from `gui.guibase.GUIBase`. They should connect to logic modules, not directly to hardware.

```python
from qtpy import QtWidgets
from core.connector import Connector
from gui.guibase import GUIBase


class MyCounterGui(GUIBase):
    counterlogic = Connector(interface='MyCounterLogic')

    def on_activate(self):
        self._logic = self.counterlogic()
        self._mw = QtWidgets.QMainWindow()
        self._label = QtWidgets.QLabel('0 cps')
        self._mw.setCentralWidget(self._label)
        self._logic.sigCountChanged.connect(self._update_count)
        self.show()

    def on_deactivate(self):
        self._logic.sigCountChanged.disconnect(self._update_count)
        self._mw.close()

    def show(self):
        self._mw.show()

    def _update_count(self, value):
        self._label.setText('{0:.0f} cps'.format(value))
```

Config example:

```yaml
gui:
    my_counter_gui:
        module.Class: 'my_counter_gui.MyCounterGui'
        connect:
            counterlogic: 'my_counter_logic'
```

Guidelines:

- Build controls from logic/device capabilities where possible.
- Keep long GUI modules split into reusable widgets and view-model/helper classes.
- Avoid direct icon paths; use a central icon/theme helper when available.
- Validate user input before calling logic.

## Creating an interfuse module

An interfuse is an adapter. Use it when existing modules nearly fit but need translation, calibration, combination, or coordinate correction.

```python
from core.connector import Connector
from core.configoption import ConfigOption
from logic.generic_logic import GenericLogic
from interface.process_interface import ProcessInterface


class ScaledProcessInterfuse(GenericLogic, ProcessInterface):
    process = Connector(interface='ProcessInterface')
    scale = ConfigOption('scale', default=1.0)

    def on_activate(self):
        self._process = self.process()

    def on_deactivate(self):
        self._process = None

    def get_process_value(self):
        return self.scale * self._process.get_process_value()
```

Guidelines:

- Keep interfuses small.
- Document what is being adapted and why.
- Prefer an interfuse over editing a stable hardware driver for one lab-specific setup.

## Checklist before adding a module

- Does the module belong in hardware, logic, GUI, interface, or interfuse?
- Are device addresses and channels in config, not code?
- Does the module declare `Connector`, `ConfigOption`, and `StatusVar` at class level?
- Does it avoid deprecated `get_connector()` and `getConfiguration()`?
- Can it be tested with dummy hardware?
- Does it shut down safely?
- Is there a config example?
- Is there a short doc page for the module or plugin?
