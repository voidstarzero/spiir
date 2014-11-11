#
# Copyright (c) 2013-2014 David McKenzie 
#
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the
# Free Software Foundation; either version 2 of the License, or (at your
# option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General
# Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.

from pylal import spawaveform
import sys
import time
import numpy
import scipy
from scipy import integrate
from scipy import interpolate
import math
from pylal import lalconstants
import pdb
import csv
from glue.ligolw import ligolw, lsctables, array, param, utils, types
from gstlal.pipeio import repack_complex_array_to_real, repack_real_array_to_complex
import pdb
from subprocess import call
import waveHandler
import random
import cbc_template_iir

class XMLContentHandler(ligolw.LIGOLWContentHandler):
	pass



def sigmasq2(mchirp, fLow, fhigh, psd_interp):
	c = lalconstants.LAL_C_SI #299792458
	G = lalconstants.LAL_G_SI #6.67259e-11
	M = lalconstants.LAL_MSUN_SI #1.98892e30
	Mpc =1e6 * lalconstants.LAL_PC_SI #3.0856775807e22
	#mchirp = 1.221567#30787
	const = numpy.sqrt((5.0 * math.pi)/(24.*c**3))*(G*mchirp*M)**(5./6.)*math.pi**(-7./6.)/Mpc
	return  const * numpy.sqrt(4.*integrate.quad(lambda x: x**(-7./3.) / psd_interp(x), fLow, fhigh)[0])


### Sometimes there will be negative or very low (~~0) frequencies ###
### at the start or the end of f. Clean them up ###
### Do this by going up through the first and last n (say, 100) ###
### and recording where the first and last incorrect values are and set them appropriately ###
def cleanFreq(f,fLower):
    i = 0;
    fStartFix = 0; #So if f is 0, -1, -2, 15, -3 15 16 17 18 then this will be 4
    while(i< 100): #This should be enough
	if(f[i] < fLower-5 or f[i] > fLower+5):  ##Say, 5 or 10 Hz)
	    fStartFix = i;
	i=i+1;

    if(fStartFix != 0):
	f[0:fStartFix+1] = fLower

    i=-30;
    while(i<0):
	#require monotonicity
	if(f[i]<f[i+1]):
	    f[i+1:]=fLower; #effectively throw away the end
	    break;
	else:
	    i=i+1;
#    print "fStartFix: %d " % (fStartFix)

def variable_parameter_comparison(xmldoc,psd_interp, outfile, param_name, param_lower, param_upper, param_num = 100,  input_mass1 = 1.4, input_mass2 = 1.4):

        '''
    VPC - Variable parameter comparison. Given a single parameter (alpha, beta or epsilon) compute, over the given range, the overlap between a template and associated SPIIR response of fixed mass (1.4-1.4)
	Keyword arguments:
	xmldoc		-- The XML document containing information about the template bank. Used to get the final frequency (not actually useful but contains other information that might be useful in the future)
	psd_interp	-- An interpolated power spectral density array. This is used to weight the signal and SPIIR response from the specifications of the LIGO/VIRGO instruments (combined)
	outfile		-- Where the output is written to
	param_name	-- String of which parameter to vary (alpha, beta, epsilon or spin (z-spin only))
	param_lower	-- The lower value of the varied parameter
	param_upper	-- The upper value of the varied parameter
	param_num	-- The number of steps to take between param_lower and param_upper
	input_mass1	-- The mass of the first body of the signal (default - canonical neutron star mass 1.4 M_sol)
	input_mass2	-- The mass of the second body of the signal (default - canonical neutron star mass 1.4 M_sol)

        ''' 
	fileName = outfile
        sngl_inspiral_table=lsctables.table.get_table(xmldoc, lsctables.SnglInspiralTable.tableName)
	fFinal = max(sngl_inspiral_table.getColumnByName("f_final"))
	fLower = 40 
	
	fileID = open(fileName,"w")
	fileID.write("snr param mass1 mass2 chirpmass numFilters \n") #clear the file
	fileID.close()

	#### Initialise constants ####
	sampleRate = 4096; padding=1.1; epsilon=0.02; alpha=.99; beta=0.25; spin=0;

	paramRange = numpy.linspace(param_lower,param_upper,num=param_num);
	
	dist=20; incl = 0;
	snr=0; m1B = 0; m2B = 0
	
	length = 2**23;

	u = numpy.zeros(length, dtype=numpy.cdouble)
	h = numpy.zeros(length, dtype=numpy.cdouble)

	matchReq = 0.975
	timing=False;
	
	timePerCompStart  = 0;
	timePerCompFin = 0;
	
	m1 = input_mass1; m2 = input_mass2;

	### We loop over parameters in the pbs function, to stop the memory leak breaking it all ###
	for paramValue in paramRange:
		if(param_name == 'epsilon' or param_name == 'eps' or param_name == 'e'):
		    epsilon = paramValue;
		elif(param_name == 'alpha' or param_name == 'a'):
		    alpha = paramValue;
		elif(param_name == 'beta' or param_name == 'b'):
		    beta = paramValue;
		elif(param_name == 'spin' or param_name == 's'):
		    spin = paramValue;
		

		mChirpSignal = (m1*m2)**(0.6)/(m1+m2)**(0.2);
		h.fill(0);
		u.fill(0);

		#### Generate signal, weight by PSD, normalise, take FFT ####
		amps,phis=waveHandler.genwave(m1,m2,dist,incl,fLower,fFinal,0,0,0,spin,0,0,0)
		fs = numpy.gradient(phis)/(2.0*numpy.pi * (1.0/sampleRate))

		cleanFreq(fs,fLower)

		if psd_interp is not None:
		    amps[0:len(fs)] /= psd_interp(fs[0:len(fs)])**0.5
		amps = amps / numpy.sqrt(numpy.dot(amps,numpy.conj(amps)));
		h[-len(amps):] = amps * numpy.exp(1j*phis);
		h *= 1/numpy.sqrt(numpy.dot(h,numpy.conj(h)))
		h = numpy.conj(numpy.fft.fft(h))
	   
		#### Now do the IIR filter for the exact mass match ####

		amp,phase=waveHandler.genwave(m1,m2,dist,incl,fLower,fFinal,0,0,0,0,0,0,0);
		f = numpy.gradient(phase)/(2.0*numpy.pi * (1.0/sampleRate))

		cleanFreq(f,fLower)

		if psd_interp is not None:
			amp[0:len(f)] /= psd_interp(f[0:len(f)])**0.5
		amp = amp / numpy.sqrt(numpy.dot(amp,numpy.conj(amp)));

		#### Get the IIR coefficients and respose ####
		a1, b0, delay = spawaveform.iir(amp, phase, epsilon, alpha, beta, padding)
		out = spawaveform.iirresponse(length, a1, b0, delay)
		out = out[::-1]
		u[-len(out):] = out;
		u *= 1/numpy.sqrt(numpy.dot(u,numpy.conj(u)))

		#http://www.mathworks.com/matlabcentral/answers/5275-algorithm-for-coeff-scaling-of-xcorr
		crossCorr = numpy.fft.ifft(numpy.fft.fft(u)*h);
		snr = numpy.abs(crossCorr).max();

		with open(fileName,"a") as fileID:
		    fileID.write("%f %f %f %f %f %f \n" % (snr, paramValue, m1, m2, mChirpSignal, len(a1)))
		    fileID.flush();

def parameter_comparison(xmldoc,psd_interp, outfile, param_name, param_value, input_minMass=1, input_maxMass=3, input_numSignals=50):

        '''
    PC - Parameter comparison. Takes a single parameter and value (e.g. eps=0.02). Generates a random spin wave and computes the overlap with SPIIR responses from templates in the supplied bank until a match of 0.97 or above is found. Repeat for a specified number of signals.
	Keyword arguments:
	xmldoc		    -- The XML document containing information about the template bank. Used to get the final frequency (not actually useful but contains other information that might be useful in the future)
	psd_interp	    -- An interpolated power spectral density array. This is used to weight the signal and SPIIR response from the specifications of the LIGO/VIRGO instruments (combined)
	outfile		    -- Where the output is written to
	param_name	    -- String of which parameter to vary (alpha, beta, epsilon or spin (z-spin only))
	param_value	    -- The value to assign to the given parameter
	input_minMass	    -- Randomly distribute signals between this and the maximum mass (default 1, lower mass for proposed aLIGO BNS search)
	input_maxMass	    -- Randomly distribute signals between the minimum mass and this (default 3, upper mass for proposed aLIGO BNS search)
	input_numSignals    -- How many signals to generate and search for (default 50)
        '''
	fileName = outfile
        sngl_inspiral_table=lsctables.table.get_table(xmldoc, lsctables.SnglInspiralTable.tableName)
	fFinal = max(sngl_inspiral_table.getColumnByName("f_final"))
	fLower = 10
	
	fileID = open(fileName,"w")
	fileID.write("snr param mass1 mass2 chirpmass numFilters \n") #clear the file
	fileID.close()

	#### Initialise constants ####
	sampleRate = 4096; padding=1.1; epsilon=0.02; alpha=.99; beta=0.25;

	if(param_name == 'epsilon' or param_name == 'eps' or param_name == 'e'):
	    epsilon = paramValue;
	elif(param_name == 'alpha' or param_name == 'a'):
	    alpha = paramValue;
	elif(param_name == 'beta' or param_name == 'b'):
	    beta = paramValue;

	
        minMass = input_minMass; maxMass = input_maxMass;
	dist=1; incl = 0;
	snr=0; m1B = 0; m2B = 0
	
	length = 2**23;

	u = numpy.zeros(length, dtype=numpy.cdouble)
	h = numpy.zeros(length, dtype=numpy.cdouble)

	matchReq = 0.97
	timing=False;
	
	timePerCompStart  = 0;
	timePerCompFin = 0;
	
	numSignals = 0; numSignalsTot = input_numSignals;
	### We loop over parameters in the pbs function, to stop the memory leak breaking it all ###
	#for s_m1 in numpy.linspace(minMass,maxMass,num=stepNumMass):
	#    for s_m2 in numpy.linspace(minMass, maxMass, num=stepNumMass):
	while numSignals < numSignalsTot:
		numSignals = numSignals + 1;
		s_m1 = random.uniform(minMass,maxMass);
		s_m2 = random.uniform(minMass,maxMass);
		mChirpSignal = (s_m1*s_m2)**(0.6)/(s_m1+s_m2)**(0.2);
		h.fill(0);
		u.fill(0);
		#### Generate signal, weight by PSD, normalise, take FFT ####
		amps,phis=waveHandler.genwave(s_m1,s_m2,dist,incl,fLower,fFinal,0,0,0,0,0,0,0)
		fs = numpy.gradient(phis)/(2.0*numpy.pi * (1.0/sampleRate))
		
		cleanFreq(fs,fLower)
		if psd_interp is not None:
		    amps[0:len(fs)] /= psd_interp(fs[0:len(fs)])**0.5

		amps = amps / numpy.sqrt(numpy.dot(amps,numpy.conj(amps)));
		h[-len(amps):] = amps * numpy.exp(1j*phis);
		h *= 1/numpy.sqrt(numpy.dot(h,numpy.conj(h)))
		h = numpy.conj(numpy.fft.fft(h))
	   
		#### Now do the IIR filter for the exact mass match ####
		m1 = s_m1 
		m2 = s_m2

		amp,phase=waveHandler.genwave(m1,m2,dist,incl,fLower,fFinal,0,0,0,0,0,0,0);
		f = numpy.gradient(phase)/(2.0*numpy.pi * (1.0/sampleRate))
		cleanFreq(f,fLower)
		if psd_interp is not None:
			amp[0:len(f)] /= psd_interp(f[0:len(f)])**0.5
		amp = amp / numpy.sqrt(numpy.dot(amp,numpy.conj(amp)));

		#### Get the IIR coefficients and respose ####
		a1, b0, delay = spawaveform.iir(amp, phase, epsilon, alpha, beta, padding)
		out = spawaveform.iirresponse(length, a1, b0, delay)
		out = out[::-1]
		u[-len(out):] = out;
		u *= 1/numpy.sqrt(numpy.dot(u,numpy.conj(u)))

		#http://www.mathworks.com/matlabcentral/answers/5275-algorithm-for-coeff-scaling-of-xcorr
		crossCorr = numpy.fft.ifft(numpy.fft.fft(u)*h);
		snr = numpy.abs(crossCorr).max();

		with open(fileName,"a") as fileID:
		    fileID.write("%f %f %f %f %f %f \n" % (snr, paramValue, s_m1, s_m2, mChirpSignal, len(a1)))
		    fileID.flush();

def construction_comparison(xmldoc,psd_interp, outfile, input_minMass = 1, input_maxMass = 3, input_numSignals = 50):
        ''' 
    CC - Construction comparison. Testing function for the new template generation method. Given a random mass pair, generate a wave and from that the SPIIR coefficients. Compute the overlap between these
        Keyword arguments:
        xmldoc		    -- The XML document containing information about the template bank. Used to get the final frequency (not actually useful but contains other information that might be useful in the future)
        psd_interp	    -- An interpolated power spectral density array. This is used to weight the signal and SPIIR response from the specifications of the LIGO/VIRGO instruments (combined)
        outfile		    -- Where the output is written to
        input_minMass	    -- Randomly distribute signals between this and the maximum mass (default 1, lower mass for proposed aLIGO BNS search)
	input_maxMass	    -- Randomly distribute signals between the minimum mass and this (default 3, upper mass for proposed aLIGO BNS search)
        input_numSignals    -- How many signals to generate and search for (default 50)
        '''
	fileName = outfile;
        sngl_inspiral_table=lsctables.table.get_table(xmldoc, lsctables.SnglInspiralTable.tableName)
	fFinal = max(sngl_inspiral_table.getColumnByName("f_final"))
	fLower = 15

	fid=open(fileName,"w")
	fid.write("snrNSNF m1 m2 mChirp numFiltersNew \n")
	fid.close()

	#### Initialise constants ####
	sampleRate = 4096; padding=1.1; epsilon=0.02; alpha=.99; beta=0.25;

	minMass = input_minMass; maxMass = input_maxMass;
	dist=1; incl = 0;
	snr=0; m1B = 0; m2B = 0
	
	length = 2**23;

	uNew = numpy.zeros(length, dtype=numpy.cdouble)
	uOld = numpy.zeros(length,dtype=numpy.cdouble)
	hNew = numpy.zeros(length, dtype=numpy.cdouble)
	hOld = numpy.zeros(length, dtype=numpy.cdouble)
	matchReq = 0.975
	timing=False;
	
	timePerCompStart  = 0;
	timePerCompFin = 0;

	numSignals = 0 ; numSignalsTot = input_numSignals;
	### We loop over parameters in the pbs function, to stop the memory leak breaking it all ###
#	for m1 in numpy.linspace(minMass,maxMass,num=stepNumMass):
#	    for m2 in numpy.linspace(minMass, maxMass, num=stepNumMass):
	while numSignals < numSignalsTot:
		numSignals = numSignals + 1;
		m1 = random.uniform(minMass,maxMass);
		m2 = random.uniform(minMass,maxMass);
		mChirp = (m1*m2)**(0.6)/(m1+m2)**(0.2);
		hNew.fill(0);
		hOld.fill(0);
		uNew.fill(0);
		uOld.fill(0);

		#### Generate signal, weight by PSD, normalise, take FFT ####
		ampNew,phaseNew=waveHandler.genwave(m1,m2,dist,incl,fLower,fFinal,0,7,0,0,0,0,0,0)
		fs = numpy.gradient(phaseNew)/(2.0*numpy.pi * (1.0/sampleRate))
		negs = numpy.nonzero(fs<fLower)
	
		cleanFreq(fs,fLower)
		if((fs<0).any()):
		    print("fs broke for masses %f %f" % (m1,m2))
		    continue
		if psd_interp is not None:
		    ampNew[0:len(fs)] /= psd_interp(fs[0:len(fs)])**0.5

		ampNew = ampNew / numpy.sqrt(numpy.dot(ampNew,numpy.conj(ampNew)));
		hNew[-len(ampNew):] = ampNew * numpy.exp(1j*phaseNew);
		hNew *= 1/numpy.sqrt(numpy.dot(hNew,numpy.conj(hNew)))
		hNew = numpy.conj(numpy.fft.fft(hNew))
	   
		#### Get the IIR coefficients and response from the new signal####
		a1New, b0New, delayNew = spawaveform.iir(ampNew, phaseNew, epsilon, alpha, beta, padding)
		outNew = spawaveform.iirresponse(length, a1New, b0New, delayNew)
		outNew = outNew[::-1]
		uNew[-len(outNew):] = outNew;
		uNew *= 1/numpy.sqrt(numpy.dot(uNew,numpy.conj(uNew)))

		#### u -> filters, h -> signal ####

		#### Overlap for new signal vs new filters ####
		crossCorr = numpy.fft.ifft(numpy.fft.fft(uNew)*hNew);
		snrNSNF = numpy.abs(crossCorr).max();

		with open(fileName,"a") as fileID:
		    fileID.write("%f %f %f %f %f\n" % (snrNSNF, m1, m2, mChirp, len(a1New)))
		    fileID.flush();

def generate_spin_mag(spinMagMax):
    return numpy.array([random.uniform(-spinMagMax,spinMagMax) for _ in range(0,6)])

def spin_comparison(xmldoc, psd_interp, outfile, input_spinMax = 0.05, input_numSignals = 2):
        ''' 
    SC - Spin comparison. Similar to parameter comparison but instead of a fixed parameter generates waves with a spin components up to a given value.
        Keyword arguments:
        xmldoc		    -- The XML document containing information about the template bank. Essentially used as a big list of mass pairs to check overlap with
        psd_interp	    -- An interpolated power spectral density array. This is used to weight the signal and SPIIR response from the specifications of the LIGO/VIRGO instruments (combined)
        outfile		    -- Where the output is written to
        input_numSignals    -- How many signals to generate and search for (default 2 - low because of memory leak)
        '''
	fileName = outfile
	sngl_inspiral_table=lsctables.table.get_table(xmldoc, lsctables.SnglInspiralTable.tableName)
	fFinal = max(sngl_inspiral_table.getColumnByName("f_final"))


	sampleRate = 4096; padding=1.1; epsilon=0.02; alpha=.99; beta=0.25; spin=0;

	spinMin = 0; spinMax = input_spinMax;
	numSignals = 0; numSignalsTotal = input_numSignals;

	fileID = open(fileName,"w")
	fileID.write("Match Signal_Mass1 Signal_Mass2 BestTmp_Mass1 BestTmp_Mass2 Signal_Chirp BestTmp_Chirp ChirpDiff SignalSymmetricMass BestTmpSym SymDiff S1x S1y S1z S2x S2y S2z TotalSpinMag BestTmpNum Tolerance TimeTakenForSignal \n") #clear the file
	fileID.close()
	
	#Does not depend on the distance
	dist=100; incl = 0;
	minMass = 1; maxMass = 3;

	fLower = 15;
	fFinal = 2047;

	length = 2**23

	matchReq = 0.97
	#timing=False
	timing=True

	timePerCompStart  = 0;
	timePerCompFin = 0;
	
	maxTmp = 20;

	debugFix = True;
	
	print("Num signals tbd: " + str(input_numSignals))

	while(numSignals < numSignalsTotal):
	    h = None;
	    h = numpy.zeros(length, dtype=numpy.cdouble)
	    numSignals += 1;

	    #Generate new parameters for the signal
	    s_m1 = random.uniform(minMass,maxMass);
	    s_m2 = random.uniform(minMass,maxMass);

	    spin = generate_spin_mag(spinMax);
	    #0 and 7 are the reference frequency (0 = maximum) and the phase nPN
	    amps,phis=waveHandler.genwave(s_m1,s_m2,dist,incl,fLower,fFinal,0,7,spin[0],spin[1],spin[2],spin[3],spin[4],spin[5])

	    ##### Apply the PSD to the signal waveform ######

	    #Get the frequency in Hz
	    fs = numpy.gradient(phis)/(2.0*numpy.pi * (1.0/sampleRate))
	    
	    #Sometimes the first few few frequencies or last few are negative due to the gradient measure
	    #Manually set them - might lose a teeny bit of accuracy but breaks otherwise

	    #numpy.savetxt("signal_freq_unfixed" + str(numSignals)+".dat",fs);
	    cleanFreq(fs,fLower)
	    #numpy.savetxt("signal_freq_fixed"+ str(numSignals)+".dat",fs);

	    if psd_interp is not None:
		    amps[0:len(fs)] /= psd_interp(fs[0:len(fs)])**0.5

	    #Normalise and conjugate the signal. 
	    #This would otherwise be done repeatedly in the loop below
	    amps = amps / numpy.sqrt(numpy.dot(amps,numpy.conj(amps)));
	    h[-len(amps):] = amps * numpy.exp(1j*phis);
	    h *= 1/numpy.sqrt(numpy.dot(h,numpy.conj(h)))
	    h = numpy.conj(numpy.fft.fft(h))

	    #42 is just a debug value - it should never occur
	    snr=0; m1B = 42; m2B = 42;

	
	    mChirpSignal = (s_m1*s_m2)**(0.6)/(s_m1+s_m2)**(0.2);
	    mSymSignal = (s_m1 * s_m2) / (s_m1 + s_m2)**2

	    
	    timePerSignalStart = time.time();
	    numTmp=0;
	    tol=0.0001; oldTol = 0;
	    symTol = 0.005; oldSymTol=0;

	    bestTmp = 0; ##The template number where the best match was found
	    while(snr < matchReq and numTmp < maxTmp):
		    u = numpy.zeros(length, dtype=numpy.cdouble)
		    for tmp, row in enumerate(sngl_inspiral_table):

			    m1 = row.mass1
			    m2 = row.mass2
			    mChirp = row.mchirp
			    mSym = (m1*m2) / (m1+m2)**2

			    #Look through the list of mass pairs till we find one with a chirp and symmetric mass below the tolerance
			    #Also must be higher than the old tolerance so that we don't repeat templates
			    if ((abs(mChirpSignal - mChirp )>tol) or (abs(mChirpSignal-mChirp) < oldTol and abs(mSymSignal-mSym) < oldSymTol) or (abs(mSymSignal - mSym) > symTol)):
				continue;

			    if timing:
				timePerTmpStart = time.time()

			    u.fill(0);
			    numTmp += 1
			    if timing:
				    timePerCompStart = time.time()	
			    

			    ##### Generate template waveform #####
			    amp,phase=waveHandler.genwave(m1,m2,dist,incl,fLower,fFinal,0,7,0,0,0,0,0,0);
			    #Apply the PSD to the waveform and normalise

			    f = numpy.gradient(phase)/(2.0*numpy.pi * (1.0/sampleRate))
#			    numpy.savetxt("template_freq_unfixed"+str(numTmp)+".dat",f);
			    cleanFreq(f,fLower)
#			    numpy.savetxt("template_freq_fixed"+str(numTmp)+".dat",f);
			    if psd_interp is not None:
				    amp[0:len(f)] /= psd_interp(f[0:len(f)])**0.5
			    amp = amp / numpy.sqrt(numpy.dot(amp,numpy.conj(amp)));


			    
			    if timing:
				    timePerCompFinish = time.time()
				    print("Time to generate template waveform: "  + str(timePerCompFinish-timePerCompStart))

			    ##### Generate the IIR filter coefficients for that template #####
			    if timing:
				    timePerCompStart = time.time();
			    a1, b0, delay = spawaveform.iir(amp, phase, epsilon, alpha, beta, padding)


			    if timing:
				    timePerCompFinish = time.time()
				    print("Time to generate IIR coefficients: " + str(timePerCompFinish - timePerCompStart));
			    

			    ##### Get the IIR filter response for that template #####
			    if timing:
				    timePerCompStart = time.time();
			    out = spawaveform.iirresponse(length, a1, b0, delay)
			    out = out[::-1]
			    u[-len(out):] = out;
			    u *= 1/numpy.sqrt(numpy.dot(u,numpy.conj(u)))

			    if timing:
				    timePerCompFinish = time.time()
				    print("Time to generate IIR response: " + str(timePerCompFinish - timePerCompStart));

			    

			    ##### Calculate the overlap between the signal and the template response #####
			    if timing:
				    timePerCompStart = time.time();
			    crossCorr = numpy.fft.ifft(numpy.fft.fft(u)*h);
			    snr2 = numpy.abs(crossCorr).max();
			    if timing:
				    timePerCompFinish = time.time();
				    print("Time to calculate SNR: " + str(timePerCompFinish - timePerCompStart));


			    ##### General SNR accounting and printing #####
			    if(snr < snr2):
				snr = snr2
				m1B = m1
				m2B = m2	
				bestTmp = numTmp

			    if(snr>matchReq):
				if timing:
				    timePerTmpEnd=time.time()
				    print("***Match found *** \nTime per template: " + str(timePerTmpEnd-timePerTmpStart))
				    print("SNR of template: " + str(snr2) + " cDiff: " + str(mChirpSignal-mChirp) + " symDiff: " + str(mSymSignal-mSym))
				break;
			    if timing:
				timePerTmpEnd=time.time()
				print("Time per template: " + str(timePerTmpEnd-timePerTmpStart))
				print("SNR of template: " + str(snr2) + " cDiff: " + str(mChirpSignal-mChirp) + " symDiff: " + str(mSymSignal-mSym))
				print("Signal m1: " + str(s_m1) + " m2: " + str(s_m2) + " Template m1: " +  str(m1) + " m2: " + str(m2))
		    
		    if(numTmp > maxTmp):
			if timing:
			    timePerSignalEnd = time.time()
			    print(" **** No match found ***")

		    #Get rid of everything. Just in case... doesn't really help.
		    if debugFix:
			amp = None; phase = None; f = None; negs = None;
			u = None; out = None;
			amps = None; phis = None; fs = None; negs = None;
			a1 = None; b0 = None; delay = None; crossCorr = None;

		    oldTol = tol;
		    oldSymTol = symTol;
		    tol = tol+0.0001;
		    symTol = symTol + 0.001

	    timePerSignalEnd = time.time()
	    spinMag1 = numpy.sqrt(numpy.dot(spin[:3],spin[:3]));
	    spinMag2 = numpy.sqrt(numpy.dot(spin[3:],spin[3:]));
	    
	    if not (numTmp ==0):
		with open(fileName,"a") as fileID:
			fileID.write("%f %f %f %f %f %f %f %f %f %f %f %f %f %f %f %f %f %f %f %f %f \n" % (snr, s_m1, s_m2, m1B, m2B,
										(s_m1*s_m2)**0.6/(s_m1+s_m2)**0.2,
										(m1B*m2B)**0.6/(m1B+m2B)**0.2,
										(s_m1*s_m2)**0.6/(s_m1+s_m2)**0.2 - (m1B*m2B)**0.6/(m1B+m2B)**0.2,
										mSymSignal, m1B*m2B / (m1B+m2B)**2, mSymSignal - m1B*m2B/(m1B+m2B)**2,
										spin[0],spin[1],spin[2],spin[3],
										spin[4],spin[5],
										spinMag1+spinMag2,
										bestTmp, tol, timePerSignalEnd-timePerSignalStart))
			#Print out as we go, for the impatient user or if the program breaks unexpectedly
			fileID.flush();




def smooth_and_interp(psd, width=1, length = 10):
        data = psd.data
        f = numpy.arange(len(psd.data)) * psd.deltaF
        ln = len(data)
        x = numpy.arange(-width*length, width*length)
        sfunc = numpy.exp(-x**2 / 2.0 / width**2) / (2. * numpy.pi * width**2)**0.5
        out = numpy.zeros(ln)
        for i,d in enumerate(data[width*length:ln-width*length]):
                out[i+width*length] = (sfunc * data[i:i+2*width*length]).sum()
        return interpolate.interp1d(f, out)

def compare_two(psd_interp, signal_m1, signal_m2, template_m1, template_m2):
        '''Compares a given template and signal'''


	fFinal = 2047; #We only go as high as the waveform anyway
	fLower = 15 


	#### Initialise constants ####
	sampleRate = 4096; padding=1.1; epsilon=0.02; alpha=.99; beta=0.25;

	dist=1
	incl = 0;
	
	length = 2**23;

	h = numpy.zeros(length, dtype=numpy.cdouble)
	u = numpy.zeros(length, dtype=numpy.cdouble)
	

	signalChirp = (signal_m1*signal_m2)**(0.6)/(signal_m1+signal_m2)**(0.2);
	templateChirp = (template_m1*template_m2)**(0.6)/(template_m1+template_m2)**(0.2);


	#### Set up the template waveform
	ampTemplate,phaseTemplate=waveHandler.genwave(template_m1,template_m2,dist,incl,fLower,fFinal,0,7,0,0,0,0,0,0)
	fTemplate = numpy.gradient(phaseTemplate)/(2.0*numpy.pi * (1.0/sampleRate))

	cleanFreq(fTemplate,fLower)

	#if((fs<0).any()):
	#    print("fs broke for masses %f %f" % (m1,m2))
	#    continue
	if psd_interp is not None:
	    ampTemplate[0:len(fTemplate)] /= psd_interp(fTemplate[0:len(fTemplate)])**0.5

	ampTemplate = ampTemplate / numpy.sqrt(numpy.dot(ampTemplate,numpy.conj(ampTemplate)));
	u[-len(ampTemplate):] = ampTemplate * numpy.exp(1j*phaseTemplate);
	u *= 1/numpy.sqrt(numpy.dot(u,numpy.conj(u)))
	u = numpy.conj(numpy.fft.fft(u))
   
	#Set up the signal waveform
	ampSignal,phaseSignal=waveHandler.genwave(signal_m1,signal_m2,dist,incl,fLower,fFinal,0,7,0,0,0,0,0,0)
	fSignal = numpy.gradient(phaseSignal)/(2.0*numpy.pi * (1.0/sampleRate))

	cleanFreq(fSignal,fLower)
	#if((fs<0).any()):
	#    print("fs broke for masses %f %f" % (m1,m2))
	#    continue
	if psd_interp is not None:
	    ampSignal[0:len(fSignal)] /= psd_interp(fSignal[0:len(fSignal)])**0.5

	ampSignal = ampSignal / numpy.sqrt(numpy.dot(ampSignal,numpy.conj(ampSignal)));
	h[-len(ampSignal):] = ampSignal * numpy.exp(1j*phaseSignal);
	h *= 1/numpy.sqrt(numpy.dot(h,numpy.conj(h)))
	h = numpy.conj(numpy.fft.fft(h))

	#### Get the IIR coefficients and response from the new signal####
	a1, b0, delay = spawaveform.iir(ampTemplate, phaseTemplate, epsilon, alpha, beta, padding)
	outTemplate = spawaveform.iirresponse(length, a1, b0, delay)
	outTemplate = outTemplate[::-1]
	u[-len(outTemplate):] = outTemplate;
	u *= 1/numpy.sqrt(numpy.dot(u,numpy.conj(u)))

	#### u -> filters, h -> signal ####

	#### Overlap for new signal vs new filters ####
	crossCorr = numpy.fft.ifft(numpy.fft.fft(u)*h);
	snr = numpy.abs(crossCorr).max();

	print("Template masses: %f, %f. Signal Masses %f, %f. sigChirp-tmpChirp: %f.SNR: %f" % (template_m1, template_m2, signal_m1, signal_m2, signalChirp - templateChirp, snr))


