# TimeTaggerFastCounter {#timetagger_fast_counter}

`hardware\swabian_instruments\timetagger_fast_counter.py` implements a gated
fast counter using a Swabian Instruments Time Tagger.

## What it uses

The module creates a `TimeTagger` device and runs a `TimeDifferences`
measurement. In the Swabian API, `TimeDifferences` accumulates histograms of
time differences between a start channel and a click channel, and can step
through several histograms with a trigger channel.

Source: <https://www.swabianinstruments.com/static/documentation/TimeTagger/api/Measurements.html>

## Channel mapping

| Qudi config option | Time Tagger role |
| --- | --- |
| `timetagger_channel_apd_0` | Photon/click channel, or first APD input when summing. |
| `timetagger_channel_apd_1` | Second APD input when summing. |
| `timetagger_channel_detect` | `start_channel`; starts each time-difference histogram. |
| `timetagger_channel_sequence` | Configured option for the pulse sequence channel. |
| `timetagger_sum_channels` | If true, `tt.Combiner` creates one virtual APD channel from both APDs. |

The current `TimeDifferences` setup uses:

```python
tt.TimeDifferences(
    tagger=self._tagger,
    click_channel=self._channel_apd,
    start_channel=self._channel_detect,
    next_channel=self._channel_detect,
    sync_channel=tt.CHANNEL_UNUSED,
    binwidth=int(np.round(self._bin_width * 1000)),
    n_bins=int(self._record_length),
    n_histograms=number_of_gates,
)
```

`binwidth` is passed to the Time Tagger in picoseconds. Qudi receives
`bin_width_s` in seconds, stores it internally as nanoseconds, then multiplies
by `1000` for the API call.

## Returned data

`get_data_trace()` returns:

```python
(np.array(self.pulsed.getData(), dtype="int64"), info_dict)
```

For a gated measurement, the array shape is:

```text
[gate_index, timebin_index]
```

Each row is one histogram. The number of rows is `number_of_gates`, and the
number of columns is derived from `record_length_s / bin_width_s`.

## Lifecycle

The usual call order is:

```text
on_activate()
configure(bin_width_s, record_length_s, number_of_gates)
start_measure()
get_data_trace()
pause_measure() / continue_measure()
stop_measure()
on_deactivate()
```

`configure()` creates the Swabian measurement object. The start, stop, pause,
continue, and read methods then operate on that object through the shared
`self.pulsed` attribute.

## Functions

### `on_activate()`

Connects Qudi to the Time Tagger hardware and initializes default fast-counter
state.

It calls `tt.createTimeTagger()` to obtain the device handle and immediately
resets the tagger with `self._tagger.reset()`. It then initializes internal
defaults:

| Attribute | Initial value | Meaning |
| --- | --- | --- |
| `_number_of_gates` | `100` | Default number of gated histograms before `configure()` is called. |
| `_bin_width` | `1` | Internal bin width placeholder, stored in nanoseconds after `configure()`. |
| `_record_length` | `4000` | Default number of bins before `configure()` is called. |
| `statusvar` | `0` | Qudi status code for unconfigured hardware. |

If `timetagger_sum_channels` is true, the module creates a Swabian
`tt.Combiner` from `timetagger_channel_apd_0` and
`timetagger_channel_apd_1`. The resulting virtual channel becomes
`self._channel_apd` and is used as the `click_channel` in the later
`TimeDifferences` measurement. If channel summing is disabled,
`self._channel_apd` is simply `timetagger_channel_apd_0`.

This method does not start an acquisition. It only prepares the device and
selects the APD input channel used for photon clicks.

### `get_constraints()`

Reports hardware limits to Qudi's fast-counter logic.

The method returns a dictionary containing:

```python
constraints["hardware_binwidth_list"] = [1 / 1000e6]
```

This corresponds to a single advertised hardware bin width of `1e-9` seconds,
or 1 ns. Qudi logic can use this list to validate requested fast-counter
settings before calling `configure()`.

The function currently does not query the Time Tagger for model-specific
limits. It also does not expose software binning options. The TODO comment in
the module suggests that software bin widths could later be added as integer
multiples of the hardware bin width.

### `on_deactivate()`

Stops and releases the active measurement object during module shutdown.

If the module state is locked, it calls `self.pulsed.stop()` first. It then
clears the measurement with `self.pulsed.clear()` and sets `self.pulsed` to
`None`.

This method assumes that `self.pulsed` already exists. In normal operation that
is true after `configure()` has been called. If activation is followed by
deactivation without configuration, this method may need an additional guard in
the future.

### `configure(bin_width_s, record_length_s, number_of_gates=0)`

Configures the Time Tagger as a gated fast counter.

Inputs:

| Parameter | Meaning |
| --- | --- |
| `bin_width_s` | Requested histogram bin width in seconds. |
| `record_length_s` | Requested duration of each gate or trace in seconds. |
| `number_of_gates` | Number of histograms to acquire, one per gate or sequence step. |

The method stores the requested settings in internal form:

```python
self._number_of_gates = number_of_gates
self._bin_width = bin_width_s * 1e9
self._record_length = 1 + int(record_length_s / bin_width_s)
```

`self._bin_width` is stored in nanoseconds, not seconds. This is why
`get_binwidth()` later multiplies it by `1e-9` to return seconds again.

The method then creates a `tt.TimeDifferences` measurement:

| `TimeDifferences` argument | Value used by this module | Effect |
| --- | --- | --- |
| `tagger` | `self._tagger` | The connected Time Tagger device. |
| `click_channel` | `self._channel_apd` | Photon clicks to histogram. |
| `start_channel` | `self._channel_detect` | Reference event for each time difference. |
| `next_channel` | `self._channel_detect` | Advances the histogram index. |
| `sync_channel` | `tt.CHANNEL_UNUSED` | No separate sync/reset channel is used. |
| `binwidth` | `int(np.round(self._bin_width * 1000))` | Bin width in picoseconds. |
| `n_bins` | `int(self._record_length)` | Number of bins per histogram. |
| `n_histograms` | `number_of_gates` | Number of gated histograms. |

According to the Swabian measurement API, measurement objects start acquiring
when they are created. The module immediately calls `self.pulsed.stop()` so
that acquisition only begins when Qudi later calls `start_measure()`.

The method sets `statusvar` to `1` for idle/configured and returns the accepted
settings as:

```python
(bin_width_s, record_length_s, number_of_gates)
```

### `start_measure()`

Starts a configured acquisition.

The method locks the Qudi module state, clears old histogram data, starts the
Swabian measurement, and sets the status code to running:

```python
self.module_state.lock()
self.pulsed.clear()
self.pulsed.start()
self.statusvar = 2
```

Clearing before `start()` means every new measurement begins with zeroed
histograms. The method returns `0` as the Qudi success code.

### `stop_measure()`

Stops the active acquisition.

If the module state is locked, the method stops the Swabian measurement and
unlocks the Qudi module state. It then sets `statusvar` to `1`, meaning idle
but configured, and returns `0`.

The accumulated histogram data is not cleared here. That means a caller can
still call `get_data_trace()` after stopping to retrieve the last acquired
data.

### `pause_measure()`

Pauses acquisition without clearing accumulated data.

If the module is locked, the method calls `self.pulsed.stop()` and sets
`statusvar` to `3`. In the Swabian API, `stop()` stops processing incoming
tags, while the measurement object and existing accumulated data remain
available. A later `continue_measure()` can restart accumulation into the same
histograms.

The method returns `0`.

### `continue_measure()`

Continues a paused acquisition.

If the module state is locked, the method calls `self.pulsed.start()` and sets
`statusvar` back to `2`. Because `continue_measure()` does not call
`clear()`, acquisition continues from the data already accumulated before the
pause.

The method returns `0`.

### `is_gated()`

Reports whether this fast counter supports gated acquisition.

The method always returns `True`. This tells Qudi pulsed-measurement logic that
the returned trace is expected to be two-dimensional, with one histogram per
gate:

```text
[gate_index, timebin_index]
```

### `get_data_trace()`

Reads the current histograms from the Time Tagger measurement.

The method calls:

```python
self.pulsed.getData()
```

and converts the result into a NumPy array with `int64` dtype:

```python
np.array(self.pulsed.getData(), dtype="int64")
```

For this module, the expected result is a two-dimensional gated histogram
array:

| Axis | Meaning |
| --- | --- |
| `gate_index` | Histogram number, controlled by `n_histograms`. |
| `timebin_index` | Time bin within the configured record length. |

The method also returns an `info_dict`:

```python
{
    "elapsed_sweeps": None,
    "elapsed_time": None,
}
```

Those values are placeholders. They are not currently read from the Swabian
measurement object, so downstream code should not rely on them for timing or
sweep counts.

### `get_status()`

Returns the module's internal status code.

The status values are:

| Code | Meaning |
| --- | --- |
| `0` | Unconfigured. |
| `1` | Idle/configured. |
| `2` | Running. |
| `3` | Paused. |
| `-1` | Error state. |

The current implementation stores this state in `self.statusvar`. It does not
ask the Time Tagger whether the underlying measurement is running.

### `get_binwidth()`

Returns the configured bin width in seconds.

During `configure()`, the module stores:

```python
self._bin_width = bin_width_s * 1e9
```

That internal value is in nanoseconds. `get_binwidth()` converts it back to
seconds:

```python
width_in_seconds = self._bin_width * 1e-9
```

The returned value should match the accepted `bin_width_s` from the most recent
`configure()` call.

## Notes

- `is_gated()` always returns `True`.
- `get_constraints()` currently advertises only a 1 ns hardware bin width.
- `elapsed_sweeps` and `elapsed_time` are not implemented and return `None`.
- `timetagger_channel_sequence` is configured but not currently used in the
  `TimeDifferences` constructor; `next_channel` is set to
  `timetagger_channel_detect`.
- `get_status()` reports only the module's own `statusvar`; it does not call
  Swabian `isRunning()`.
- `on_deactivate()` assumes `self.pulsed` exists, which is normally true only
  after `configure()`.
