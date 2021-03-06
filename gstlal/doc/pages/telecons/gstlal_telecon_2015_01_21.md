\page gstlaltelecons20150121page Telecon January 21, 2015

\ref gstlalteleconspage

[TOC]

\section agenda Agenda

-# Follow-up of IMBH non-zero-lag events from cWB
-# BBH lock-ups
-# Search computational resources

\section attendance Attendance

\section minutes Minutes

-# IMBH followup:

 Les: Been asked by IMBH group to follow up on non-zero-lag events.  My understanding is that, if given the time slide vector and the time of the event, I could re-filter about an hour of data around the event, then run this through the likelihood generated by the full 2-month run.
Chad: Yes, look at output of re-filtering first to see if it's worth rerunning calc_likelihood

-# Tjonnie BBH lockups:

 Tjonnie+Kent: Processes stalling with BBH pipeline.  Happens typically with injection runs, but also non-injection runs.  Gave command that locks up (not doing anything, no CPU usage, for days/weeks) over email. Using IMRPhenomB waveforms. This has been happening for at least a month. Using a gstlal version from master, but also seeing stalling processes for ER6 release.
Chad: Doesn't happen for BNS.
Steve: We need to get a stable BBH pipeline.
Jolien: Debug with printf statement. Also, in line with what Steve suggested for a stable pipeline, it might be time for a nightly test suite that could be run to test if changes to master break things.
Chad: Not convinced that a code change is causing this issue...
Tjonnie: Worked for ER5 release though.
Chad: Okay, then we should be able to figure out what changed to cause this stalling.  I will also try running your Makefile and can meet with you later to discuss.
Kent: The pipeline does work with MDC injections.
Jolien: The IMR waveforms are ALL WRONG... grumble grumble... But seriously, they need some major sanity checks.
Kent+Patrick: This should be brought to waveform call.

-# Computational resources

 Chad: Yes, we can only run at CIT and UWM, but we just need a cluster to support dynamic slots
Steve: Yes, it's not an issue, but it needs to be done! This has been on the todo list for years, let's do it. How do I start?
Patrick: Suggested course of action... Pick a workflow that you can run on some reseource, then choose a cluster that you want to run on, email DASWG for small issues, but then start attending LDG call and sort through other problems with them.  Atlas might be where to start. 
Chad: I can write a patch to adjust condor submit files to make it more vanilla so that if a cluster has dynamic slots, they will match.
Kent: The general queu at CIT is not well configured, in fact none of the general pool supports dynamic slots
Patrick: UWM has 2000 cores for gstlal to run on, which is enough for offline searches.  Maybe we just try to get these working perfectly.

 Les: How should we be calculating the "engineering factor"
Chad: Remove fudge factors if possible, and explain each input.
Steve: What changes in our calculations between O1, O2, and O3? Rerun calculation with new fmin?
Chad: If you think so... It's to get accurate numbers. Should get throughput for injection runs too.
Patrick: Best benchmarking is for O1. How much will this change for final configuration?
Chad: Throughput only changes by 10%, so dominated by larger template bank.  BBH and IMBH will have to measure this seperately.

TODOs:
1. Chad will run BBH Makefile and converse with Tjonnie about stalling processes.
2. Chad will  write a patch to adjust condor submit files to make it more vanilla so that if a cluster has dynamic slots, they will match.
3. Les and Tjonnie will coordinate computational requirements calculations for BBH and IMBH searches

