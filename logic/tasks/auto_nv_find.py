# -*- coding: utf-8 -*-
"""
Automated NV center finding task for the TaskRunner.

This task wraps the AutoNVFinderLogic for use with Qudi's TaskRunner system.
It can be configured in the task runner config to run automatically or
on-demand through the task runner GUI.

See documentation/automation/ for full documentation.

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

from logic.generic_task import InterruptableTask
import time


class Task(InterruptableTask):
    """Automated NV center finding task.

    Configuration example in the Qudi config file:

        tasklogic:
            module.Class: 'taskrunner.TaskRunner'
            tasks:
                auto_nv_find:
                    module: 'auto_nv_find'
                    pausetasks: ['scan']
                    needsmodules:
                        auto_nv_finder: 'auto_nv_finder_logic'
                    config:
                        threshold_sigma: 5.0
                        max_candidates: 20
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        print('Task {0} added!'.format(self.name))

    def startTask(self):
        """Start the auto NV finding pipeline.

        Applies any config overrides and starts the auto-finder.
        """
        finder = self.ref['auto_nv_finder']

        # Apply config overrides if provided
        if 'threshold_sigma' in self.config:
            finder.set_threshold(self.config['threshold_sigma'])
        if 'max_candidates' in self.config:
            finder.max_candidates = int(self.config['max_candidates'])
        if 'min_intensity' in self.config:
            finder.set_min_intensity(self.config['min_intensity'])

        # Start the auto-find pipeline
        finder.start_auto_find()

    def runTaskStep(self):
        """Wait for the auto-finder to complete.

        @return bool: True if still running, False if done.
        """
        time.sleep(0.5)
        return self.ref['auto_nv_finder'].is_running

    def pauseTask(self):
        """Pause the auto-finder (stops after current candidate)."""
        self.ref['auto_nv_finder'].stop_auto_find()

    def resumeTask(self):
        """Resume is not supported — must restart."""
        pass

    def cleanupTask(self):
        """Stop the auto-finder if still running."""
        if self.ref['auto_nv_finder'].is_running:
            self.ref['auto_nv_finder'].stop_auto_find()

    def checkExtraStartPrerequisites(self):
        """Check that the auto-finder and its dependencies are ready."""
        finder = self.ref['auto_nv_finder']
        # Check that the finder is not already running
        if finder.is_running:
            return False
        # Check that the scanner is not locked
        try:
            scanner = finder.confocallogic()
            if scanner.module_state() == 'locked':
                return False
        except Exception:
            return False
        return True

    def checkExtraPausePrerequisites(self):
        """Can always pause (stop after current candidate)."""
        return True
