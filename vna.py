from instrument import Instrument, runlater
import scpi
from scpi import onoff
from pyvisa.errors import VisaIOError
import math
import numpy as np
from scipy.optimize import curve_fit
from scipy.stats import linregress
import time
import copy

class VNA(Instrument):
    def __init__(self, resource):
        super().__init__()
        self.res = scpi.Wrapper(resource)

    @staticmethod
    def match_device(devname):
        model = devname.split(",")[1].strip()
        return model == "E5071C" or model == "E5071B" or model == "N5232A"

    def setup(self, config):
        self.cfg = VNAConfig(config)

        self.res.reset()
        
        devname = self.res.query("*IDN?")
        self.model = devname.split(",")[1].strip()
        
        #TODO: Markers not yet supported on 
        if self.model == "N5232A":
            self.cfg.use_markers = False
        
        self.res.write(":CALC1:PAR1:DEF {}", "S21")
        self.res.write(":INIT1:CONT {}", onoff(True))
        if not self.cfg.use_markers:
            if self.model == "N5232A":
                self.res.write(":TRIG:SOUR MAN")
            else:
                self.res.write(":TRIG:SOUR BUS")
        self.res.write(":SENS1:SWE:TYPE {}", "SEGM")
        self.res.write(":SENS1:SWE:DELAY {}", 0.001)
        self.res.write(":SENS1:SWE:GEN {}", "STEP")

        self.set_segments(self.cfg.segments)
        time.sleep(1.0)
        self.res.write(":DISP:WIND1:TRAC1:Y:AUTO")
        if self.cfg.use_markers:
            self.res.write(":CALC1:MARK:BWID {}", onoff(True))
            self.res.write(":CALC1:MARK:FUNC:MULT:TYPE {}", "PEAK")
            self.res.write(":CALC1:MARK:FUNC:EXEC")
            self.res.write(":CALC1:MARK:FUNC:MULT:TRAC {}", onoff(True))
            #for idx, segment in enumerate(self.cfg.segments, 1):
            #    self.setup_marker(segment.f0, idx)
        else:
            #self.res.write(":CALC1:MARK:FUNC:MULT:TYPE {}", "PEAK")
            #self.res.write(":CALC1:MARK:FUNC:EXEC")
            #self.res.write(":CALC1:MARK:FUNC:MULT:TRAC {}", onoff(True))
            #self.res.write(":CALC1:MARK:BWID {}", onoff(True))
            pass

        self.last_sample = None

    def sample(self, elapsed):
        if not self.cfg.use_markers:
            if self.model == "N5232A":
                self.res.write(":INIT:IMM")
            else:
                self.res.write(":TRIG:SING")
            self.res.query("*OPC?")

        data = Sample()
        if self.cfg.use_markers:
            try:
                for i in range(len(self.cfg.segments)):
                    bw, f0, q, il = self.get_marker_data(i+1)
                    data.bw.append(bw)
                    data.f0.append(f0)
                    data.q.append(q)
                    data.il.append(il)
            except VisaIOError:
                return None

            arr = [data.bw,data.f0,data.q]
            if self.last_sample:
                if self.last_sample == arr:
                    return None
            self.last_sample = arr
        else:
            cplx = np.array(self.get_sweep_data())
            cplx = cplx.reshape((-1,2)).T
            ampl = np.sqrt(cplx[0]**2 + cplx[1]**2)
            freq = self.get_freq_data()
            
            start = 0
            lost_track = False
            for seg in self.cfg.segments:
                if seg.enabled:
                    f = freq[start:seg.points+start]
                    a = ampl[start:seg.points+start]
                    start += seg.points
                    try:
                        bw, f0, q, il = lorentz_fit(f,a)
                        data.bw.append(bw)
                        data.f0.append(f0)
                        data.q.append(q)
                        data.il.append(il)
                        data.freq.append(f)
                        data.ampl.append(a)
                    except (RuntimeError, ValueError):
                        lost_track = True
                        if self.cfg.track_freq and self.cfg.track_enabled:
                            slope, intercept, rvalue, pvalue, stderr = linregress(f, a)
                            if(slope > 0):
                                seg.f0 += seg.span
                            else:
                                seg.f0 -= seg.span
                else: #segment not enabled
                    data.bw.append(None)
                    data.f0.append(None)
                    data.q.append(None)
                    data.il.append(None)
                    data.freq.append(None)
                    data.ampl.append(None)
                        
            if lost_track:
                if self.cfg.track_freq and self.cfg.track_enabled:
                    self.set_segments(self.cfg.segments)
                return None

        if (self.cfg.track_freq or self.cfg.track_span) and self.cfg.track_enabled:
            retracked = False
            for seg, f0, bw in zip(self.cfg.segments, data.f0, data.bw):
                if not seg.enabled:
                    continue
                trackf, tracks = track_window(seg.f0, seg.span, f0, bw,
                                              bw_factor=self.cfg.get_bw_factor())
                if (trackf and self.cfg.track_freq) or (tracks and self.cfg.track_span):
                    retracked = True
                    if self.cfg.track_freq:
                        seg.f0 = f0
                    if self.cfg.track_span:
                        seg.span = bw*self.cfg.get_bw_factor()
            if retracked:
                self.set_segments(self.cfg.segments)
                if self.cfg.use_markers:
                    # Force complete trigger
                    if self.model == "N5232A":
                        self.res.write(":TRIG:SOUR MAN")
                        self.res.write(":INIT:IMM")
                    else:
                        self.res.write(":TRIG:SOUR BUS")
                        self.res.write(":TRIG:SING")
                    self.res.query("*OPC?")
                    # Reset back to default
                    self.res.write(":TRIG:SOUR INT")

        return data

    def cleanup(self):
        self.res.close()
        pass

    def get_headers(self):
        h = ["Frequency {}/Hz".format(s.name) for s in self.cfg.segments]
        h += ["Q factor {}".format(s.name) for s in self.cfg.segments]
        h += ["Insertion loss {}/dB".format(s.name) for s in self.cfg.segments]
        return h

    def format_sample(self, data):
        return data.f0 + data.q + data.il

    def set_segments(self, segments, channel=1):
        enacount = 0
        if self.model == "N5232A":
            data = ["SSTOP", None]
            for s in segments:
                if s.enabled:
                    enacount+=1
                    data += [1, s.points, s.f0, s.span, s.ifbw, 0, s.power]
            data[1] = enacount
            
            self.res.write(":SENS{}:SEGM:BWID:CONT {}", channel, onoff(True))
            self.res.write(":SENS{}:SEGM:POW:CONT {}", channel, onoff(True))
            self.res.write_ascii_values(":SENS{}:SEGM:LIST", data, channel)
        else:
            #[<buf>,<stim>,<ifbw>,<pow>,<del>,<time>,<segm>]
            data = [5, 1, 1, 1, 0, 0, None]
            for s in segments:
                if s.enabled:
                    enacount+=1
                    data += [s.f0, s.span, s.points, s.ifbw, s.power]
            data[6] = enacount
            self.res.write_ascii_values(":SENS{}:SEGM:DATA", data, channel)

    def setup_marker(self, freq, marker=1, channel=1):
        self.res.write(":CALC{}:MARK{} {}", channel, marker, onoff(True))
        self.res.write(":CALC{}:MARK{}:X {}", channel, marker, freq)
        self.res.write(":CALC{}:MARK{}:FUNC:TYPE {}", channel, marker, "PEAK")
        self.res.write(":CALC{}:MARK{}:FUNC:TRAC {}", channel, marker, onoff(True))
        self.res.write(":CALC{}:MARK:BWID {}", channel, onoff(True))

    def get_marker_data(self, marker=1, channel=1):
        return self.res.query_ascii_values(":CALC{}:MARK{}:BWID:DATA?", channel, marker)

    def get_sweep_data(self, channel=1):
        if self.model == "N5232A":
            return self.res.query_ascii_values(":CALC{}:DATA? SDAT", channel)
        else:
            return self.res.query_ascii_values(":CALC{}:DATA:SDAT?", channel)

    def get_freq_data(self, channel=1):
        if self.model == "N5232A":
            return self.res.query_ascii_values(":CALC{}:X?", channel)
        else:
            return self.res.query_ascii_values(":SENS{}:FREQ:DATA?", channel)

    @runlater
    def set_segment_enabled(self, segment, enabled):
        self.cfg.segments[segment].enabled = enabled
        self.set_segments(self.cfg.segments)

    @runlater
    def set_bw_factor_override(self, factor):
        self.cfg.bw_factor_override = factor
        
    @runlater
    def set_tracking_override(self, enabled):
        self.cfg.track_enabled = enabled
        
    @runlater
    def reset_tracking(self):
        for segment in self.cfg.segments:
            segment.f0 = segment.f0_default
            segment.span = segment.span_default
        self.set_segments(self.cfg.segments)

class VNAConfig(object):
    def __init__(self, config):
        self.segments = []
        for n, s in config["segments"].items():
            self.segments.append(Segment(n, s["f0"], s["span"], s["points"],
                                         s["ifbw"], s["power"]))
        self.segments.sort(key=lambda seg: seg.f0)
        self.track_freq = config["track_frequency"]
        self.track_span = config["track_span"]
        self.use_markers = config["use_markers"]
        self.bw_factor = config["bandwidth_factor"]
        self.bw_factor_override = None
        self.track_enabled = True
        
    def get_bw_factor(self):
        if self.bw_factor_override is not None:
            return self.bw_factor_override
        else:
            return self.bw_factor


class Segment(object):
    def __init__(self, name, f0=2.495e9, span=50e6, points=51, ifbw=1000, power=0):
        self.name = name
        self.f0 = self.f0_default = f0
        self.span = self.span_default = span
        self.points = points
        self.ifbw = ifbw
        self.power = power
        self.enabled = True

class Sample(object):
    def __init__(self):
        self.bw = []
        self.f0 = []
        self.q = []
        self.il = []
        self.freq = []
        self.ampl = []

def track_window(center, span, f0, bw, center_err=0.8,
                 span_err=0.3, bw_factor=8.0):
    ferr = math.fabs(center-f0) + bw/2 #Ensure +- bw markers stay within
                                       #center_err of window
    retrackf = ferr > span*center_err*0.5

    retracks = (bw*bw_factor)/span > 1 + span_err
    retracks = retracks or span/(bw*bw_factor) > 1 + span_err
    return retrackf, retracks

def lorentz_fn(x, f0, bw, pmax):
    return pmax/np.sqrt(1 + (4*((x-f0)/bw)**2))

def lorentz_fit(freq, ampl, f0=0.5, bw=0.5, pmax=1.0):

    freq = np.array(freq)
    ampl = np.array(ampl)
    maxa = np.max(ampl)
    norma = ampl/maxa
    weights = norma*norma

    minf = np.min(freq)
    maxf = np.max(freq)
    fspan = maxf-minf
    normf = (freq-minf)/fspan
    a1=0.0
    (f0, bw, pmax), pcov = curve_fit(lorentz_fn, normf, norma,
                                     (f0, bw, pmax))
    f0 = (f0*fspan)+minf
    bw = np.fabs(bw)*fspan
    pmax = 20*np.log10(pmax*maxa)
    return bw, f0, f0/bw, pmax
