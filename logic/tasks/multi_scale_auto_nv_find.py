# -*- coding: utf-8 -*-
"""
Multi-Scale Automated NV center finding task for the TaskRunner.

This task wraps the MultiScaleAutoNVFinderLogic for use with Qudi's TaskRunner system.
It can be configured in the task runner config to run automatically or
on-demand through the task runner GUI.

Qudi is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""

from logic.generic_task import InterruptableTask
import time


class Task(InterruptableTask):
    """Multi-Scale Automated NV center finding task.

    Configuration example in the Qudi config file:

        tasklogic:
            module.Class: 'taskrunner.TaskRunner'
            tasks:
                multi_scale_auto_nv_find:
                    module: 'multi_scale_auto_nv_find'
                    pausetasks: ['scan']
                    needsmodules:
                        multi_scale_auto_nv_finder: 'multi_scale_auto_nv_finder_logic'
                    config:
                        coarse_fov_um: 200.0
                        max_regions_per_run: 10
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        print('Task {0} added!'.format(self.name))

    def startTask(self):
        """Start the multi-scale auto NV finding pipeline.

        Applies any config overrides and starts the orchestrator.
        """
        finder = self.ref['multi_scale_auto_nv_finder']

        # Apply config overrides if provided
        if 'coarse_fov_um' in self.config:
            finder.coarse_fov_um = float(self.config['coarse_fov_um'])
        if 'max_regions_per_run' in self.config:
            finder.max_regions_per_run = int(self.config['max_regions_per_run'])

        # Start the multi-scale pipeline
        finder.start_multi_scale_find()

    def runTaskStep(self):
        """Wait for the multi-scale finder to complete.

        @return bool: True if still running, False if done.
        """
        time.sleep(0.5)
        return self.ref['multi_scale_auto_nv_finder'].state != 'idle'

    def pauseTask(self):
        """Pause the multi-scale finder (stops after current step)."""
        self.ref['multi_scale_auto_nv_finder'].stop_multi_scale_find()

    def resumeTask(self):
        """Resume is not supported — must restart."""
        pass

    def cleanupTask(self):
        """Stop the multi-scale finder if still running."""
        if self.ref['multi_scale_auto_nv_finder'].state != 'idle':
            self.ref['multi_scale_auto_nv_finder'].stop_multi_scale_find()

    def checkExtraStartPrerequisites(self):
        """Check that the multi-scale finder and its dependencies are ready."""
        finder = self.ref['multi_scale_auto_nv_finder']
        # Check that the finder is not already running
        if finder.state != 'idle':
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
        """Can always pause."""
        return True
