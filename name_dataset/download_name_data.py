import marimo

__generated_with = "0.24.0"
app = marimo.App(width="medium")


@app.cell
def _():
    from pathlib import Path

    import polars as pl

    return Path, pl


@app.cell
def _():
    import kagglehub

    # Download source name data, pinned to a specific version. Pinning
    # (a) makes kagglehub skip its version-resolution API call when the
    # cached version is present, and (b) keeps the derived CSVs (and CI's
    # seed smoke) stable even if the dataset publishes new versions.
    # Bump the version number intentionally to pick up new data.
    path = kagglehub.dataset_download("erpel1/forenames-and-surnames-with-gender-and-country/versions/2")

    print("Path to dataset files:", path)
    return (path,)


@app.cell
def _(Path, path, pl):
    forenames = pl.read_csv(Path(path) / "forenames.csv")
    us_forenames = forenames.filter(pl.col("country") == "US").drop("country")
    us_forenames.write_csv("us_forenames.csv")
    us_forenames
    return


@app.cell
def _(Path, path, pl):
    surnames = pl.read_csv(Path(path) / "surnames.csv")
    us_surnames = surnames.filter(pl.col("country") == "US").drop("country")
    us_surnames.write_csv("us_surnames.csv")
    us_surnames
    return


if __name__ == "__main__":
    app.run()
