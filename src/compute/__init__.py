"""Compute layer — metric constructions (§5). One module per panel lands here
per the §8 build order. Each build() reads lake data (store.lake_conn /
Parquet), computes the metric + its trailing percentile (util), downsamples to
display resolution (util.downsample_display), and writes the display JSON
(store.write_display). Missing computes simply leave last-good JSON in place."""
