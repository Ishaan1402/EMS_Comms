/* SAS checks the AI: Eir's suggested ESI against each benchmark's reference label.
   Input: evals/benchmark/results/esi_pairs.csv (one row per model call; written by
   python -m evals.benchmark.score). Upload it to SAS Studio (or point FILENAME at it) and run.
   The weighted kappas here should equal results/metrics.json (stats.py uses SAS's formulas). */

filename pairs '/home/&sysuserid./esi_pairs.csv';   /* change to where you uploaded the file */

proc import datafile=pairs out=work.pairs dbms=csv replace;
  guessingrows=max;
run;

/* Primary result: repeat 1 of each case, cases that got an ESI from the AI. */
data work.primary;
  set work.pairs;
  where repeat = 1 and not missing(ai_esi);
  length analysis $ 24;
  analysis = catx('/', dataset, 'all'); count = 1; output;
  if ems_arrival = 1 and dataset in ('mimic', 'ktas') then do;
    analysis = catx('/', dataset, 'ems'); output;
  end;
  keep analysis reference_level ai_esi nurse_ktas count;
run;

/* Zero-count cells so every table is 5 x 5 (levels 1-5), matching the Python weights. */
data work.square;
  set work.primary(keep=analysis) ;
  by analysis notsorted;
  if first.analysis then do reference_level = 1 to 5;
    do ai_esi = 1 to 5; count = 0; output; end;
  end;
run;
proc sort data=work.square nodupkey; by analysis reference_level ai_esi; run;

data work.agree;
  set work.primary work.square;
run;
proc sort data=work.agree; by analysis; run;

title "Eir suggested ESI vs reference (repeat 1)";
proc freq data=work.agree;
  by analysis;
  weight count / zeros;
  tables reference_level * ai_esi / agree(wt=fc) norow nocol nopercent;
  test wtkap;
run;

/* Human yardstick on the KTAS data: the triage nurses' KTAS vs the experts' (same scale). */
title "KTAS triage nurse vs KTAS experts";
proc freq data=work.pairs(where=(repeat = 1 and dataset = 'ktas'));
  tables reference_level * nurse_ktas / agree(wt=fc) norow nocol nopercent;
run;
title;
