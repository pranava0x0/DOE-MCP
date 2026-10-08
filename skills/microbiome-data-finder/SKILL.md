---
name: microbiome-data-finder
description: >
  Find DOE-linked microbiome studies and samples for a kind of environment
  or a place, and say where the sequence data behind them is held. Use when
  someone asks "is there soil microbiome data from Washington", "which
  studies sampled hydraulic-fracturing fluids", or "how many samples does
  the NEON soil study have", or needs sample coordinates and dates before
  requesting sequence data.
capabilities:
  - bio.search_studies
  - bio.get_study
  - bio.biosamples
  - earth.search_datasets
servers:
  - doe-bio
  - doe-earth
credentials: []
---

# Microbiome data finder

The National Microbiome Data Collaborative (NMDC) holds metadata for the
studies it has ingested and for each of their samples: where and when a
sample was taken, what environment it came from, and the DOIs that lead to
the reads and assemblies at JGI and EMSL. It holds no sequence data, and
it is not every microbiome study ever run. A walk that ends in "NMDC has
nothing" has said something about NMDC.

## The walk

1. **Decide whether the question is about studies or samples.** Most NMDC
   studies carry no ecosystem classification of their own; their samples
   do. "Soil microbiomes" is therefore a sample question, answered with
   `bio.search_biosamples` and `ecosystem_type="Soil"` or
   `env_medium="soil"`. A named project, investigator or method is a study
   question, answered with `bio.search_studies` and `text`.

2. **Narrow by place and date with the sample filters.** `place` is a
   substring of the location as the submitter wrote it, such as
   "USA: Columbia River, Washington". A state name can miss samples
   recorded only by site or by coordinates, so report the filter used and
   do not present the count as every sample from that state. `collected`
   matches the start of the date, "2017" or "2017-06".

3. **Read the study behind the samples.** Each sample names its study id.
   `bio.get_study` returns the investigators with ORCID, the funding
   statements, the DOIs, and how many samples the study holds in total.
   The DOIs are where the sequence data is; pass them on rather than
   describing the data from its metadata.

4. **Check the environmental repository when NMDC is thin.** ESS-DIVE
   holds DOE's environmental-system-science datasets, some with microbial
   measurements that never became NMDC studies. `earth.search_datasets`
   with the topic finds them. Keep the two lists separate: a matching word
   does not make an ESS-DIVE dataset and an NMDC study the same campaign.

5. **Compose.** Studies with their sample counts and DOIs; for a sample
   question, the number matched under the stated filters, a few samples
   with coordinates and dates, and the studies they belong to. Close with
   what is not here: sequence data, and studies NMDC has not ingested.

## What this skill will not do

It will not describe sequence content from metadata, treat a place
substring match as a complete count for a region, merge an ESS-DIVE
dataset into an NMDC study on a shared keyword, or pass on an
investigator's contact details, which the tools drop.

## Bench tasks

| # | Task | Passes when |
|---|---|---|
| 1 | "Is there soil microbiome data from Washington State?" | Uses a biosample search with the soil ecosystem and the place filter, reports the matched count with the filter text, names the studies the samples belong to, and says a place match can miss samples. |
| 2 | "How many samples are in NEON's soil metagenome study, and where is the sequence data?" | Reads the study, reports its biosample count, and gives the study's DOIs as the route to the data rather than describing the reads. |
| 3 | "Which studies sampled shale gas wells?" | Finds the deep-subsurface shale study by text and lists its samples by study id. |
| 4 | "Find microbiome data from zzzznotaplacezzzz." | Reports that NMDC holds no matching sample and that this describes NMDC's holdings, then checks ESS-DIVE before concluding. |
| 5 | "Are there permafrost microbial datasets?" | Searches ESS-DIVE as well as NMDC and keeps the two result lists separate. |
