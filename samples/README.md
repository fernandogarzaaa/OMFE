# Sample CSVs

* `revenue_onlyfans_export.csv` — matches the default mapping:
  `ofm import-revenue samples/revenue_onlyfans_export.csv --tenant demo --mapping '{"date":"Date","creator":"Creator","campaign":"Campaign","gross":"Gross","net":"Net","new_subs":"New Subs"}'`
* `revenue_infloww_style.csv` — different headers and US dates:
  `--mapping '{"date":"Day","creator":"Model Name","campaign":"Tracking Link","gross":"Earnings (USD)","new_subs":"Subscribers","date_format":"%m/%d/%Y"}'`
  (net is derived with `net_ratio_if_missing`, default 0.8).
* `leads_import_example.csv` — example applicant rows for demos/automation testing (all fictional, 18+).
