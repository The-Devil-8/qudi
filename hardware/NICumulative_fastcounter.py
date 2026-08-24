# -*- coding: utf-8 -*-
"""
This file contains the Qudi hardware module for the NIFastcounter that does cumulative counting and is compatible with pulsed_measurement_logic_CHANGED.

Qudi is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

Qudi is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with Qudi. If not, see <http://www.gnu.org/licenses/>.

Copyright (c) the Qudi Developers. See the COPYRIGHT.txt file at the
top-level directory of this distribution and at <https://github.com/Ulm-IQO/qudi/>
"""

import ctypes
from ctypes import byref, POINTER
import numpy as np
import time
from qtpy import QtCore
import PyDAQmx as daq

from core.module import Base
from core.configoption import ConfigOption
from core.util.modules import get_main_dir
from core.util.mutex import Mutex
from interface.slow_counter_interface import SlowCounterInterface
from interface.slow_counter_interface import SlowCounterConstraints
from interface.slow_counter_interface import CountingMode
from interface.fast_counter_interface import FastCounterInterface

import nidaqmx
from nidaqmx.constants import AcquisitionType, TaskMode, CountDirection, Edge
#from nidaqmx._task_modules.channel_collection import ChannelCollection
from nidaqmx.stream_readers import CounterReader

class NIFastCounter(Base, SlowCounterInterface, FastCounterInterface):
    """ Hardware class to use the NI card as a fast counter.

    Example config for copy-paste:

    NIfastcounter:
        module.Class: 'NIfastcounter.NIFastCounter'
		PD_Channel: '/Dev1/PFI8' # photon counter channel
		Clock_Channel: '/Dev1/Ctr2'
		Digital_Channel: '/Dev1/PFI0' # sync counter channel
		contrast_based_Aquisition: True

    """
    PD_Channel = ConfigOption(name='PD_Channel', default='/Dev3/PFI1', missing='warn')
    Clock_Channel = ConfigOption(name='Clock_Channel', default='/Dev3/Ctr2', missing='warn')
    Digital_Channel = ConfigOption(name='Digital_Channel', default='/Dev3/PFI2', missing='warn')
    _contrast_based = ConfigOption('contrast_based_Aquisition', True, missing='warn') #JSS: added
    sigReadoutNI = QtCore.Signal()
    sigStart = QtCore.Signal()

    def __init__(self, config, **kwargs):
        # Modify ONLY for PulsedMeasurements
        super().__init__(config=config, **kwargs)
        print('PD channel')
        print(self.PD_Channel)
        print(self.Clock_Channel)
        # Just some default values:
        self._bin_width_ns = 2
        self._record_length_ns = 100  # 100 *1e9 #JSS: modified
        self.firsttimeNI = True

        # locking for thread safety
        self.threadlock = Mutex()

    def on_activate(self):
        print("on_activate")
        """ Activate and establish the connection to NI card and initialize.
        """

        self.sigStart.connect(self.start_measure)
        self.sigReadoutNI.connect(self.get_fresh_data_loop, QtCore.Qt.QueuedConnection)  # ,QtCore.Qt.QueuedConnection
        time.sleep(0.2)

    def on_deactivate(self):
        print("on_deactivate")
        """ Deactivates and disconnects the device.
        """
        self.sigReadoutNI.disconnect()

    def start(self, acq_time):
        print("start")
        """ Start acquisition for 'acq_time' ms.
        """
        try:
            self.Clock.stop()  # self.Clock.StopTask() #JSS: Line 124 from s_a.py
            self.Clock.close()  # self.Clock.ClearTask() #JSS: CHECK THIS!!
            # print('task stoped1')
        except Exception as e:
            print("Clock not stopped in start()")
            pass

        self.Clock = nidaqmx.Task()  # daq.Task() #JSS: modified using Line 77 from s_a.py
        print("clock created in start()")

        my_clock_channel = self.Clock_Channel
        ch = self.Clock.ci_channels.add_ci_count_edges_chan(my_clock_channel, initial_count=0, edge=Edge.RISING,
                                                                count_direction=CountDirection.COUNT_UP)  # JSS: Line 78 from s_a.py
        ch.ci_count_edges_term = self.PD_Channel  # JSS: Line 80 from s_a.py

        # JSS: Check this!: not sure if the following two snippets should be placed here Or in "get_data_trace" #JSS: CERTAINLY NOT IN DATA TRACE, MUST BE HERE ONLY
        self.Clock.timing.cfg_samp_clk_timing(self.Sampling_rate, source=self.Digital_Channel, active_edge=Edge.RISING,
                                            sample_mode=AcquisitionType.FINITE,
                                              samps_per_chan=self.samples)  # JSS: Line 81 from s_a.py; with "samps_per_chan" set as "samples";  #JSS: CHECK THIS!! Im not sure what the first argument '1000' does
        self.reader = CounterReader(self.Clock.in_stream)  # JSS: adapted from Line 83 #JSS: should I move this to get_data_trace?

        try:
            self.Clock.start()  # JSS: Line 122 #JSS: Check This!! Not sure about the placing, but think its to arm the counter before the PB outputs


        except Exception as e:
            print('exception Happened:', e)
            raise
            #    #JSS: I was going to blindly change the following snippets, but then remembered that "self.Clock" was literally a clock in the original code, but here its a counter! Hence not sure about keeping the following snippets..
            #    self.Clock.stop() #self.Clock.StopTask() #JSS: Line 124
            #    self.Clock.close() #self.Clock.ClearTask() #JSS: CHECK THIS!!
            #self.Clock.start() #self.Clock.StartTask() #JSS


    def stop_device(self):
        print("stop_device")
        """ Stop the measurement."""
        self.meas_run = False
        self.firsttimeNI = 1
        try:
            self.Clock.stop()  # self.Clock.StopTask() #JSS: modified this
            self.Clock.close()  # self.Clock.ClearTask() #JSS: CHECK THIS!!
            print('Task Stopped2')
        except Exception as e:
            self.log.exception(e)

    # =========================================================================
    #  Functions for the SlowCounter Interface
    # =========================================================================

    def set_up_clock(self, clock_frequency=None, clock_channel=None):
        print('set_up_clock')
        """Ensure Interface compatibility.
        """

        return 0

    def set_up_counter(self, counter_channels=1, sources=None,
                       clock_channel=None):
        print("set_up_counter")
        """ Ensure Interface compatibility. The counter allows no set up.

        @param string counter_channel: Set the actual channel which you want to
                                       read out. Default it is 0. It can
                                       also be 1.
        @param string photon_source: is not needed, arg will be omitted.
        @param string clock_channel: is not needed, arg will be omitted.

        @return int: error code (0:OK, -1:error)
        """
        self._count_channel = counter_channels

        return 0

    def get_counter_channels(self):
        print('get_counter_channels')
        """ Return one counter channel. """
        return ['Ctr0']

    def get_constraints(self):
        print('get_constraints')
        """ Get hardware limits of NI device.

        @return SlowCounterConstraints: constraints class for slow counter

        FIXME: ask hardware for limits when module is loaded
        """
        constraints = dict()
        # the unit of those entries are seconds per bin. In order to get the
        # current binwidth in seonds use the get_binwidth method.
        constraints['hardware_binwidth_list'] = [1e-9, 10e-9, 50e-9, 100e-9, 0.5e-6, 1e-6, 1.5e-6, 2e-6]
        # TODO: think maybe about a software_binwidth_list, which will
        #      postprocess the obtained counts. These bins must be integer
        #      multiples of the current hardware_binwidth

        return constraints

    def get_counter(self, samples=None):
        print('get_counter')
        """ Returns the current counts per second of the counter.

        @param int samples: if defined, number of samples to read in one go

        @return float: the photon counts per second
        """
        time.sleep(0.05)
        # return [self.get_count_rate(self._count_channel)]
        return 0

    def close_counter(self):
        print('close_counter')
        """ this command will do
        nothing and is only here for SlowCounterInterface compatibility.

        @return int: error code (0:OK, -1:error)
        """
        return 0

    def close_clock(self):
        print('close_clock')
        """this command will do
        nothing and is only here for SlowCounterInterface compatibility.

        @return int: error code (0:OK, -1:error)
        """
        return 0

    # =========================================================================
    #  Functions for the FastCounter Interface
    # =========================================================================

    # FIXME: The interface connection to the fast counter must be established!

    def configure(self, bin_width_ns, record_length_ns, number_of_gates=0, stop_sweep=0): #JSS: the stop sweep is redundant here, yet lets keep it JIC!
        print('configure')
        self.startSweep = 0
        self.mycounter = 1
        self.numberofsweeps = 0
        """
        Configuration of the fast counter.
        bin_width_ns: Length of a single time bin in the time trace histogram
                      in nanoseconds.
        record_length_ns: Total length of the timetrace/each single gate in
                          nanoseconds.
        number_of_gates: Number of gates in the pulse sequence. Ignore for
                         ungated counter.
        stop_sweep: optional, number of sweeps after which to stop measurement.
                               0 means run indefinitely (default behaviour).
        """
        # print(self.get_binwidth())

        self.testStatue = 0
        self._bin_width_ns = bin_width_ns * 1e9  # the input is in second I believe and not nanosecond #JSS: will purpose this for clock frequnecy alone, so will leave this as it is
        self._record_length_ns = record_length_ns  # record_length_ns * 1e9  #JSS: modifying to the number of pulse sequences #JSS: CHECK THIS!!
        self._num_of_lasers = number_of_gates #int(10)  # JSS: added this;  connection to the **GUI** is also done!!!
        if self._contrast_based == True:
            self._num_of_lasers = number_of_gates * 4
        else:
            self._num_of_lasers = number_of_gates * 2

        print("NIFastCounter._num_of_lasers:", self._num_of_lasers)
        self.data_trace = np.zeros(int(self._num_of_lasers))  # np.zeros(int(np.size(self.mybins)) - 1, dtype=np.int64)  # modified #JSS: modifiid for the new method
        self.data_trace_helper = self.data_trace  # modified
        self.data_trace_helper20 = np.array([], dtype=np.int64)

        self._number_of_gates = number_of_gates  # JSS: CHECK THIS!! This might connect to _num_of_lasers, so you can remove that variable #PS: Already did it!
        self.startflag = 0
        # FIXME: actualle only an unsigned array will be needed. Change that later. WE fixed it!Not sure though!

        self.firsttimeNI = 1
        self.result = []

        ####################### NI Card
        Resolution = self._bin_width_ns * 1e-9  # it should be in seconds
        self.ACQtime = int(self._record_length_ns)  # self._record_length_ns * 1e-9  # 10 second is ok, ACQ time in seconds   #JSS: modified for its "integer-al" role

        self.period = Resolution * 2  # period/2 is the resolution
        self.Sampling_rate = np.floor(1 / Resolution)


        #JSS: Not sure whethere these are best placed here
        self.samples = int(self.ACQtime * self._num_of_lasers) + 1 #JSS:ADDDED

        #JSS: the following could be safely placed in start, actually it might be wrong to place it here, as before Clock is created this is skipped (try except) and after start "configure" is never caleed
        '''
        try:
            self.Clock.timing.cfg_samp_clk_timing(1000, source=self.Digital_Channel, active_edge=Edge.RISING,
                                            sample_mode=AcquisitionType.FINITE,
                                              samps_per_chan=self.samples)  # JSS: Line 81 from s_a.py; with "samps_per_chan" set as "samples";  #JSS: CHECK THIS!! Im not sure what the first argument '1000' does
            #reader = CounterReader(self.Clock.in_stream)  # JSS: adapted from Line 83
        except Exception as e: # JSS:
            print("Timing configuration failed:", e) # JSS:
            pass #raise  # JSS:
        '''
        #To counter above you can just bring the "clock creation" over here, But that again causes the problem that: Clock should be armed immediately after "RUN measurement" and not any time before that,
        #As then you might play with "pulser on" and meanwhile daq might start obtaining data...

        self.counts = np.empty(self.samples, dtype=np.uint32)  # JSS: adapted from Line 84

        return bin_width_ns, record_length_ns, number_of_gates, stop_sweep

    def get_status(self):
        print("get status")
        """
        Receives the current status of the Fast Counter and outputs it as
        return value.
        0 = unconfigured
        1 = idle
        2 = running
        3 = paused
        -1 = error state
        """
        #JSS: there is only digital mode, so removing these
        '''
        if self.useNIcard or self.useNIcardDI == 1:
            return 1
        else:
            return -1
        '''
        return 1 #JSS: added

    def pause_measure(self):
        print('pause_measure')

        """
        Pauses the current measurement if the fast counter is in running state.
        """
        try:
            temp = self.numberofsweeps
            self.stop_measure()
            self.meas_run = False
            self.numberofsweeps = temp
        except:
            print('measurement not pauses')

    def continue_measure(self):
        print('continue_measure')

        """
        Continues the current measurement if the fast counter is in pause state.
        """
        self.meas_run = True
        self.firsttimeNI = 0  #JSS: added Not just for the sake of differentiating from start and stop measure, but also to make use of continue/pause in the p_m module...
        self.start(self._record_length_ns)  # self.start(self._record_length_ns / 1e6)  # /1e6 was here #JSS: modified for "integer-al" self._record_length_ns

    def is_gated(self):
        """
        Boolean return value indicates if the fast counter is a gated counter
        (TRUE) or not (FALSE).
        """
        return True #False #JSS: changed to allow predefined_pulses to set the counter pulses

    def get_binwidth(self):

        """
        returns the width of a single timebin in the timetrace in seconds
        """
        width_in_seconds = self._bin_width_ns * 1e-9
        print('inside get binwidth width in sec')
        print(width_in_seconds)

        # FIXME: Must be implemented
        return width_in_seconds

    def get_data_trace(self):
        print("get data trace")
        """
        in this method for Analog signal, we find the pulses, and add the new mwasured data for each pulse at each sweep.
        """

        timeout = 10.0

        _RWTimeout = 2  # check this

        try:
            self.reader.read_many_sample_uint32(self.counts, number_of_samples_per_channel=self.samples, timeout=10000.0)  # JSS: Line 121
        except Exception as e:
            print("measurement? too early bud:/", e)
            info_dict = {'elapsed_sweeps': self.numberofsweeps,
                         'elapsed_time': None}  # TODO : implement that according to hardware capabilities
            return self.data_trace, info_dict

        if not self.meas_run:
            print('measurement is done2')
        # self.sigReadoutNI.emit() # loop


        Laser_cumulative = self.counts.copy()  # JSS: added
        print("Laser_cumulative", Laser_cumulative, len(Laser_cumulative))


        try:
            if self.firsttimeNI == 1:
                print("yay entering the self.firsttimeNI == 1 condition :_o")
                self.LaserSumhelper = np.zeros(self.samples)  # np.zeros(ArraySize + 1) #JSS: Modified
                self.firsttimeNI = 0
            self.LaserSumhelper += Laser_cumulative
            print("LaserSumhelper", self.LaserSumhelper, len(self.LaserSumhelper))
            #JSS: end


            self.data_trace = self.LaserSumhelper/(self.numberofsweeps+1)
            if self.numberofsweeps < 300000 and self.meas_run:  # NI card number of Sweeps
                self.numberofsweeps = self.numberofsweeps + 1

        except Exception as e:
            self.log.exception(e)
        info_dict = {'elapsed_sweeps': self.numberofsweeps,
                     'elapsed_time': None}  # TODO : implement that according to hardware capabilities
        return self.data_trace, info_dict

    def start_measure(self):

        print('start_measure')

        self.meas_run = True  # to start the measurement u need to pass this serting
        self.numberofsweeps = 0 #JSS: this was already there in stop_measure, but then why is not updating in the gui, so try addding here
        self.start(
            int(self._record_length_ns))  # self.start(int(self._record_length_ns / 1e6))  # Measurement time in millisec (unit ms) it is acq time which should be between 1 to... ms #JSS: modified
        self.sigReadoutNI.emit()
        time.sleep(1e-2) #JSS: coz there is an asynchrony at the start, not sure why, AT line 814 in pulsed_measurwement_logic


    def stop_measure(self):
        '''
        try:
            self.reader.read_many_sample_uint32(self.counts, number_of_samples_per_channel=self.samples,
                                                timeout=10000.0)  #JSS: added, pending: I dont like this low effort juggad, need a good workaround for "Finite acquisition or generation has been stopped before the requested number of..."
        except Exception as e:
            print("no NI reader created yet IG")
            pass
        ''' #JSS: add this only if youre adding start() in get_data_trace
        try:
            print('stop device stopeed')
            self.Clock.stop()  # self.Clock.StopTask() #JSS: modified
            self.Clock.close()  # self.Clock.ClearTask() #JSS: modified
        except Exception as e:
            pass #self.log.exception(e)
        print('stop_measure')
        self.numberofsweeps = 0
        """ By setting the Flag, the measurement should stop.  """
        self.firsttimeNI = 1
        self.meas_run = False

    def get_fresh_data_loop(self):
        print("get_fresh_data_loop")

        """ This method will be run infinitely until the measurement stops. """

        if not self.meas_run:
            with self.threadlock:
                # self.unlock()
                #    print('unlock should be defined') #unlock should be defined
                try:
                    self.stop_device()
                    self.numberofsweeps = 0
                    self.mycounter = 1
                # print('measurement is done')
                except:
                    print('measurement is not stopped')
                return