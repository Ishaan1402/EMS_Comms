/* SAS checks the AI: Eir's suggested ESI against each benchmark's reference label, the headline
   safety numbers, and a fairness check by sex and age band. Run it in SAS Studio.

   Input: the shared table Public.EIR_BENCH_CALLS (one row per AI call), loaded by
   python -m evals.benchmark.to_sas push. Without CAS, upload evals/benchmark/results/esi_pairs.csv
   instead and use the PROC IMPORT lines below (the headline and fairness sections need the CAS table).
   Every number printed here should equal results/metrics.json (stats.py uses SAS's formulas). */

cas eir;                                 /* a CAS session for this program */
libname pub cas caslib="Public";         /* the shared caslib as a SAS library */

/* After a CAS restart the table is saved on disk but not in memory: load it back. */
proc cas;
  table.tableExists result=r / caslib="Public" name="EIR_BENCH_CALLS";
  if r.exists == 0 then do;
    table.loadTable / caslib="Public" path="EIR_BENCH_CALLS.sashdat"
                      casOut={caslib="Public", name="EIR_BENCH_CALLS", promote=true};
  end;
run;
quit;

data work.pairs;
  set pub.EIR_BENCH_CALLS;
run;

/* Without CAS:
filename pairs '/home/&sysuserid./esi_pairs.csv';
proc import datafile=pairs out=work.pairs dbms=csv replace; guessingrows=max; run;
*/

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

title "Eir suggested ESI vs reference (repeat 1), linear weights";
proc freq data=work.agree;
  by analysis;
  weight count / zeros;
  tables reference_level * ai_esi / agree(wt=ca) norow nocol nopercent;
run;

/* Human yardstick on the KTAS data: the triage nurses' KTAS vs the experts' (same scale). */
title "KTAS triage nurse vs KTAS experts";
proc freq data=work.pairs(where=(repeat = 1 and dataset = 'ktas'));
  tables reference_level * nurse_ktas / agree(wt=fc) norow nocol nopercent;
run;
proc freq data=work.pairs(where=(repeat = 1 and dataset = 'ktas'));
  tables reference_level * nurse_ktas / agree(wt=ca) norow nocol nopercent;
run;
title;

/* The headline: KTAS ambulance arrivals the experts rated 1-2 (critical or emergent).
   Expect the AI: Given 1-2 138, No level (Needs review) 14, Given 3-5 2; the nurses: 3-5 15. */
data work.urgent;
  set work.pairs;
  where repeat = 1 and reference_level <= 2 and dataset in ('ktas', 'mimic');
  length nurse_placed $ 9;
  if not missing(nurse_ktas) then nurse_placed = ifc(nurse_ktas <= 2, 'Given 1-2', 'Given 3-5');
  ai_put_at_3_5 = (urgent_outcome = 'Given 3-5');
run;

title "KTAS ambulance arrivals rated 1-2 by the experts: where Eir put them";
proc freq data=work.urgent;
  where dataset = 'ktas' and ems_arrival = 1;
  tables urgent_outcome nurse_placed / nocum;
run;

/* Fairness: of the urgent visits, did the AI put one sex or age band at 3-5 more often?
   Expect no significant difference (two-sided Fisher p: KTAS sex 0.12, age 0.06; MIMIC sex 0.55,
   age 0.12); the direction flips between the two datasets. Small counts: this cannot rule out a gap. */
proc sort data=work.urgent; by dataset; run;
title "Urgent visits Eir put at 3-5, by sex and by age band (Fisher's exact test)";
proc freq data=work.urgent;
  by dataset;
  tables (sex age_band) * ai_put_at_3_5 / fisher norow nocol nopercent;
run;

/* SAS's bias assessment on the visits the AI gave a level for: "urgent" is reference 1-2 and the
   prediction is "the AI gave 1-2". Equal opportunity is the gap in urgent visits flagged urgent.
   python -m evals.benchmark.sas_fairness runs the same action and saves EIR_BENCH_BIAS_*. */
title "fairAITools.assessBias: KTAS and MIMIC, urgent vs not";
proc cas;
  loadactionset "fairAITools";
  do grp over {"SEX", "AGE_BAND"};
    program = "P_URGENT = AI_URGENT; P_NOT_URGENT = 1 - AI_URGENT; length URGENT $ 3; " ||
              "URGENT = ifc(REFERENCE_URGENT = 1, 'Yes', 'No');";
    fairAITools.assessBias /
      table={caslib="Public", name="EIR_BENCH_CALLS",
             where="PRIMARY = 1 and AI_GAVE_LEVEL = 1 and DATASET in ('ktas', 'mimic')",
             computedVars={{name="P_URGENT"}, {name="P_NOT_URGENT"}, {name="URGENT"}},
             computedVarsProgram=program},
      modelTableType="NONE", response={name="URGENT"}, event="Yes", responseLevels={"Yes", "No"},
      predictedVariables={{name="P_URGENT"}, {name="P_NOT_URGENT"}},
      sensitiveVariable={name=grp};
  end;
run;
quit;
title;

cas eir terminate;
