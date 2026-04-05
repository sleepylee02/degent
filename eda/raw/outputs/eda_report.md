# Multi-Source Movie EDA Summary

Generated at: 2026-03-27 11:58:42

## Output Structure

- MovieLens detailed EDA: [ml_32m/eda_report.md](ml_32m/eda_report.md)
- MovieLens dataset note (KO): [ml_32m/dataset_description_report_ko.md](ml_32m/dataset_description_report_ko.md)
- Genome 2021 detailed EDA: [genome_2021/eda_report.md](genome_2021/eda_report.md)
- Genome 2021 dataset note (KO): [genome_2021/dataset_description_report_ko.md](genome_2021/dataset_description_report_ko.md)

## Side-By-Side Snapshot

| Source | Catalog movies | Ratings | Users | Reviews | Tag signal | Timestamps | Best role |
| --- | --- | --- | --- | --- | --- | --- | --- |
| MovieLens ml-32m | 87,585 | 32,000,204 | 200,948 | - | 2,000,072 free-text tags | Yes | Primary interaction log |
| Genome 2021 Movie Dataset | 84,661 | 28,490,116 | 247,383 | 2,624,608 | 832,896 tag applications + survey/scores | No | Semantic enrichment |

## Joinability

| Metric | Value |
| --- | --- |
| Safe join key | `ml-32m.movieId = genome_2021.item_id` |
| Overlapping movie ids | 73,476 |
| Genome catalog covered by MovieLens ids | 86.79% |
| MovieLens catalog covered by Genome ids | 83.89% |

User ids should not be assumed to align across the two sources. Use movie ids for integration and keep user spaces separate unless provenance has been verified independently.

## Main Takeaways

- MovieLens is the better source for sequential recommendation because it has timestamps and 32,000,204 rating events.
- Genome 2021 is the better source for item semantics because it adds 2,624,608 IMDb reviews, survey labels, and precomputed tag-relevance scores.
- Both sources are long-tail. In MovieLens, the top 1% of movies account for about 54.88% of ratings. In Genome 2021, the top 1% of movies account for about 50.92% of ratings.
