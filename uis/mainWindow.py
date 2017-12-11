from utils import getResourcePath
from PyQt5.uic import loadUiType
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QFileDialog, QDialog, QErrorMessage, QMessageBox, QListWidgetItem
from PyQt5.QtCore import QTimer, Qt
from uis.editInstrumentsDialog import EditInstrumentsDialog
import sys
import os

MainWindowUI, MainWindowBase = loadUiType(getResourcePath('ui/mainWindow.ui'))


class MainWindow(MainWindowBase, MainWindowUI):
    def __init__(self, backend, configLoader, parent=None):
        MainWindowBase.__init__(self, parent)
        self.setupUi(self)

        self.backend = backend
        self.configLoader = configLoader
        self.cfgfile = None
        self.configModified = False

        self.instrumentDataWindows = {}
        self.instrumentConfigWindows = {}
        self.instrumentIcons = {}

        self.updateTimer = QTimer()
        self.updateTimer.timeout.connect(self.timerTimeout)
        self.actionNewConfig.triggered.connect(lambda: self.newConfig())
        self.actionOpenConfig.triggered.connect(lambda: self.openConfig())
        self.actionSaveConfig.triggered.connect(lambda: self.saveConfig())
        self.actionSaveConfigAs.triggered.connect(lambda: self.saveConfigAs())

        self.configureInstruments.clicked.connect(self.editInstruments)
        self.runButton.clicked[bool].connect(self.runButtonClicked)
        self.recordButton.clicked[bool].connect(self.recordButtonClicked)
        self.recordDuration.valueChanged.connect(self.recordDurationChanged)

        self.addSample.clicked.connect(self.addSampleClicked)
        self.removeSample.clicked.connect(self.removeSampleClicked)
        self.moveUp.clicked.connect(self.moveUpClicked)
        self.moveDown.clicked.connect(self.moveDownClicked)
        self.samplesList.itemChanged.connect(self.samplesListItemChanged)
        self.samplesList.currentRowChanged.connect(self.samplesListRowChanged)

        self.running = False

    def newConfig(self):
        #TODO warn if modified

        cfgfile, _ = QFileDialog.getSaveFileName(self, "Select File", filter="Configuration files (*.json)")
        try:
            with open(cfgfile, 'w') as fp:
                config = self.configLoader.loadData({
                    'instruments': {
                        'vna0': {
                            'type': 'vna',
                            'model': 'simulated',
                            'segments': {
                                'TM010' : {
                                    'type': 'Electric',
                                    'f0': 2500700000.0,
                                    'span': 1000000.0,
                                    'points': 201,
                                    'ifbw': 1000.0,
                                    'power': 0.0
                                }
                            }
                        }
                    }
                })
                self.backend.set_config(config, os.path.dirname(cfgfile))
                self.enableConfigWidgets()
                self.updateConfigWidgets(self.backend.config)
                self.cfgfile = cfgfile
                self.configLoader.saveFile(fp, self.backend.config)
        except (FileNotFoundError, ValueError) as err:
            msg = QErrorMessage()
            msg.setWindowTitle("Config File Error")
            msg.showMessage(str(err))
            msg.exec_()

    def openConfig(self, cfgfile=None):
        if cfgfile is None:
            cfgfile, _ = QFileDialog.getOpenFileName(self, "Select File", filter="Configuration files (*.json)")
        try:
            with open(cfgfile, 'r') as fp:
                config = self.configLoader.loadFile(fp)
                self.backend.set_config(config, os.path.dirname(cfgfile))
                self.enableConfigWidgets()
                self.updateConfigWidgets(self.backend.config)
                self.cfgfile = cfgfile
        except (FileNotFoundError, ValueError) as err:
            msg = QErrorMessage()
            msg.setWindowTitle("Config File Error")
            msg.showMessage(str(err))
            msg.exec_()

    def saveConfig(self):
        if self.cfgfile is not None:
            with open(self.cfgfile, 'w') as fp:
                self.configLoader.saveFile(fp, self.backend.config)

    def saveConfigAs(self):
        cfgfile, _ = QFileDialog.getSaveFileName(self, "Select File", filter="Configuration files (*.json)")
        with open(cfgfile, 'r') as fp:
            self.configLoader.saveFile(fp, self.backend.config)
            self.cfgfile = cfgfile

    def editInstruments(self):
        editInstrumentDialog = EditInstrumentsDialog(self.backend.config, self.instrumentIcons, self.instrumentConfigWindows)
        if editInstrumentDialog.exec_() == QDialog.Accepted:
            self.backend.set_config(editInstrumentDialog.config, os.path.dirname(self.cfgfile))
            self.updateConfigWidgets(self.backend.config)
            self.configModified = True

    def registerInsturmentType(self, name, dataWindow, configWindow=None):
        self.instrumentDataWindows[name] = dataWindow
        if configWindow is not None:
            self.instrumentConfigWindows[name] = configWindow

    def registerInstrumentIcon(self, configType, icon):
        self.instrumentIcons[configType] = icon

    def enableConfigWidgets(self):
        self.runButton.setEnabled(True)
        self.configureInstruments.setEnabled(True)
        self.samplesList.setEnabled(True)
        self.addSample.setEnabled(True)
        self.actionSaveConfig.setEnabled(True)
        self.actionSaveConfigAs.setEnabled(True)

    def updateConfigWidgets(self, config):
        self.samplesList.clear()
        self.samplesList.addItems(config.samples)
        for i in range(self.samplesList.count()):
            self.samplesList.item(i).setFlags(Qt.ItemIsSelectable | Qt.ItemIsEditable | Qt.ItemIsEnabled)

    def timerTimeout(self):
        updatefns = {}
        for n in range(self.instrumentTabs.count()):
            updatefns[self.instrumentTabs.tabText(n)] = self.instrumentTabs.widget(n).addSample
        if not self.backend.process_samples(updatefns):
            self.recordButton.setChecked(False)
            self.incrementSample()

        for n in range(self.instrumentTabs.count()):
            self.instrumentTabs.widget(n).refresh()

    def runButtonClicked(self, running):
        if running:
            self.running = True
            self.backend.start()
            for name, cfg in self.backend.config.instruments.items():
                instrument = self.backend.instruments[name]
                widget = self.instrumentDataWindows[cfg.type_](instrument)
                self.instrumentTabs.addTab(widget, name)
            self.updateTimer.start(500)
            self.updateSampleButtons(self.samplesList.currentRow())
            self.configureInstruments.setEnabled(False)
        else:
            self.running = False
            self.backend.stop()
            self.updateTimer.stop()
            self.recordButton.setEnabled(False)
            self.instrumentTabs.clear()
            self.updateSampleButtons(self.samplesList.currentRow())
            self.configureInstruments.setEnabled(True)

    def recordButtonClicked(self, recording):
        if recording:
            text = self.samplesList.currentItem().text()
            self.backend.start_logging(text)
        else:
            self.backend.stop_logging()
            self.incrementSample()

    def recordDurationChanged(self, val):
        self.backend.config.record_duration = val

    def addSampleClicked(self):
        samples = self.backend.config.samples
        sampleName = "New sample"
        self.backend.config.samples.append(sampleName)
        item = QListWidgetItem(sampleName)
        item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEditable | Qt.ItemIsEnabled)
        self.samplesList.addItem(item)
        self.configModified = True

        self.samplesList.setFocus()
        self.setSampleIndex(len(samples)-1)

    def removeSampleClicked(self):
        samples = self.backend.config.samples
        row = self.samplesList.currentRow()
        del samples[row]
        self.samplesList.takeItem(row)
        self.configModified = True

            self.samplesList.setFocus()

            if row >= len(samples):
                row = len(samples)-1
            if row < 0:
            self.updateSampleButtons(row)
                return

        self.setSampleIndex(row)

    def moveUpClicked(self):
        samples = self.backend.config.samples
        row = self.samplesList.currentRow()
            if row == 0 or row >= len(samples):
                return
        item = samples.pop(row)
        samples.insert(row-1, item)
        listItem = self.samplesList.takeItem(row)
        self.samplesList.insertItem(row-1, listItem)
        self.configModified = True

            self.samplesList.setFocus()
        self.setSampleIndex(row-1)

    def moveDownClicked(self):
        samples = self.backend.config.samples
        row = self.samplesList.currentRow()
            if row+1 >= len(samples):
                return
        item = samples.pop(row)
        samples.insert(row+1, item)
        listItem = self.samplesList.takeItem(row)
        self.samplesList.insertItem(row+1, listItem)
        self.configModified = True

            self.samplesList.setFocus()
        self.setSampleIndex(row+1)

    def samplesListItemChanged(self, item):
        widget = item.listWidget()
        row = widget.row(item)
        samples = self.backend.config.samples
        if samples[row] != item.text():
            samples[row] = item.text()
            self.configModified = True

    def samplesListRowChanged(self, row):
        self.updateSampleButtons(row)

    def setSampleIndex(self, row):
        self.samplesList.setCurrentRow(row)
        self.updateSampleButtons(row)

    def updateSampleButtons(self, row):
        samples = self.backend.config.samples
        valid = row >= 0 and row < len(samples)
        self.recordButton.setEnabled(valid and self.running)
        self.removeSample.setEnabled(valid)
        self.moveUp.setEnabled(valid and row > 0)
        self.moveDown.setEnabled(valid and row < len(samples)-1)

    def incrementSample(self):
        if self.autoIncrementSample.isChecked():
            samples = self.backend.config.samples
            row = self.samplesList.currentRow()
            row += 1
            if row >= len(samples):
                row = 0
            self.setSampleIndex(row)

    def closeEvent(self, event):
        self.backend.stop()
        self.updateTimer.stop()
