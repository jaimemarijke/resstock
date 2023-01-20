"""
Postprocess in.area_median_income to EUSS Round 1 results (2022-09-16) (applicable to baseline and 10 packages)

This script requires resstock-estimation conda env and access to resstock-estimation GitHub repo

Author: lixi.liu@nrel.gov 
Date: 2022-10-06
"""
from pathlib import Path
import sys
import numpy as np
import pandas as pd
from functools import cache

resstock_estimation = Path(__file__).resolve().parents[2] / "resstock-estimation"
sys.path.append(resstock_estimation)
from utils.parameter_option_maps import ParameterOptionMaps

POM = ParameterOptionMaps()


data_dir = Path(__file__).resolve().parent / "data"

### Add AMI tags to EUSS
def process_ami_lookup(geography):
    """
    geography option: PUMA, State, Census Division

    """
    if geography == "County and PUMA":
        file = "income_bin_representative_values_by_county_puma_occ_FPL.csv"
    elif geography == "PUMA":
        file = "income_bin_representative_values_by_puma_occ_FPL.csv"
    elif geography == "State":
        file = "income_bin_representative_values_by_state_occ_FPL.csv"
    elif geography == "Census Division":
        file = "income_bin_representative_values_by_cendiv_occ_FPL.csv"
    elif geography == "Census Region":
        file = "income_bin_representative_values_by_cenreg_occ_FPL.csv"
    elif geography == "National":
        file = "income_bin_representative_values_by_occ_FPL.csv"
    else:
        raise ValueError(f"geography={geography} not supported")

    ami_lookup = pd.read_csv(data_dir / file)
    deps = ["Occupants", "Federal Poverty Level", "Income"]
    deps = [geography] + deps if geography != "National" else deps
    income_col = "weighted_median"
    ami_lookup = ami_lookup[
        ~ami_lookup[deps + [income_col]].isna().any(axis=1)
    ].reset_index(drop=True)[deps + [income_col]]

    if geography == "PUMA":
        # map puma to version in EUSS
        puma_file_full_path = data_dir / "spatial_puma_lookup.csv"
        if puma_file_full_path.exists():
            puma_map = pd.read_csv(puma_file_full_path)
        else:
            puma_map = pd.read_parquet(data_dir / "spatial_block_lookup_table.parquet")
            puma_map = puma_map[
                ["puma_tsv", "nhgis_2010_puma_gisjoin"]
            ].drop_duplicates()
            puma_map.to_csv(puma_file_full_path, index=False)

        ami_lookup[geography] = ami_lookup[geography].map(
            puma_map.set_index("puma_tsv")["nhgis_2010_puma_gisjoin"]
        )

    ami_lookup = ami_lookup.rename(columns={income_col: "rep_income"})

    return ami_lookup, deps


def get_median_from_bin(value_bin, lower_multiplier=0.9, upper_multipler=1.1):
    if "<" in value_bin:
        return float(value_bin.strip("<")) * lower_multiplier
    if "+" in value_bin:
        return float(value_bin.strip("+")) * upper_multipler

    return np.mean([float(x) for x in value_bin.split("-")])


def assign_representative_income(df):
    non_geo_cols = ["in.occupants", "in.federal_poverty_level", "in.income"]
    geographies = [
        "County and PUMA",
        "PUMA",
        "State",
        "Census Division",
        "Census Region",
        ]
    geo_cols = ["in." + geo.lower().replace(" ", "_") for geo in geographies]
    df_section = df[geo_cols+non_geo_cols].copy()
    geographies += ["National"]

    # map rep income by increasingly large geographic resolution
    for idx in range(len(geographies)):
        geography = geographies[idx]
        ami_lookup, deps = process_ami_lookup(geography)
        if idx == len(geographies)-1:
            keys = non_geo_cols
        else:
            keys = [geo_cols[idx]]+non_geo_cols

        if idx == 0:
            # map value by County and PUMA
            df_section = df_section.join(
                ami_lookup.set_index(deps),
                on=keys,
            )
        else:
            # map remaining value
            df_section.loc[df_section["rep_income"].isna(), "rep_income"] = (
                df_section[keys]
                .astype(str)
                .agg("-".join, axis=1)
                .map(
                    ami_lookup.assign(
                        key=ami_lookup[deps].astype(str).agg("-".join, axis=1)
                    ).set_index("key")["rep_income"]
                )
            )
            if len(df_section[df_section["rep_income"].isna()]) == 0:
                print(f"Highest resolution used for mapping representative income: {geography}")
                break

    assert len(df_section[df_section["rep_income"].isna()]) == 0, df_section[df_section["rep_income"].isna()]

    df["rep_income"] = df_section["rep_income"]

    return df


def map_ami_by_income_limits_by_county(df):
    income_limits_df = pd.read_csv(data_dir / "fy2019_hud_income_limits_by_county.csv")
    income_limits_df.set_index("county_gis", inplace=True)

    max_occupants = 8
    bin_edges, _ = POM.load_area_median_income_bins()

    def get_pivoted_income_limits_df(income_limits_df):
        """Reformat income limit table"""
        multi_cols = [
            (int(c.split("_")[1]), int(c.removeprefix("l").split("_")[0]))
            for c in income_limits_df.columns
        ]
        income_limits_df.columns = pd.MultiIndex.from_tuples(
            multi_cols, names=["occupant", "income_limit"]
        )
        melted_idf = (
            income_limits_df.transpose()
            .reset_index()
            .melt(id_vars=["occupant", "income_limit"])
        )
        pivoted_income_limits_df = melted_idf.pivot(
            index=["occupant", income_limits_df.index.name],
            columns="income_limit",
            values="value",
        ).reset_index()
        return pivoted_income_limits_df

    pidf = get_pivoted_income_limits_df(income_limits_df)

    @cache
    def get_extrapolation_factor_from_8occupants(occupant):
        """Get multiplier for extrapolating income limits beyond
        max_occupants available in map, extrapolation is based
        on 8-occupant income limits
        """
        if occupant > max_occupants:
            factor = POM._household_size_adjustment(
                occupant
            ) / POM._household_size_adjustment(8)
            return factor
        return 1

    location_col = "in.county"
    income_col = "rep_income"
    occupant_col = "rep_occupants"
    output_col = "in.area_median_income"

    df_section = df[[location_col, income_col, occupant_col]].copy()
    df_section["extrapolation_factor"] = df[occupant_col].map(
        get_extrapolation_factor_from_8occupants
    )
    df_section["clipped_occupant"] = df[occupant_col].map(
        lambda occ: occ if occ <= max_occupants else 8
    )

    df_income_limit = df_section.merge(
        pidf,
        how="left",
        left_on=[location_col, "clipped_occupant"],
        right_on=[income_limits_df.index.name, "occupant"],
    ).drop(columns=["clipped_occupant", income_limits_df.index.name])

    # update income limits to actual occupant number
    # using extrapolation_factor, round to nearest 50
    df_income_limit[bin_edges] = (
        df_income_limit[bin_edges].mul(
            df_income_limit["extrapolation_factor"], axis=0
        )
        / 50
    ).round() * 50
    conds = df_income_limit[[income_col]].values < df_income_limit[bin_edges].values
    bin_labels = [f"0-{bin_edges[0]}%"] + [
        f"{bin_edges[i-1]}-{bin_edges[i]}%" for i in range(1, len(bin_edges))
    ]  # all except last bin
    cases = np.repeat(np.array(bin_labels, ndmin=2), len(conds), axis=0)
    default_bin = [f"{bin_edges[-1]}%+"]
    ami_bin = np.select(conds.transpose(), cases.transpose(), default=default_bin)

    df[output_col] = ami_bin
    # nully ami_bin where income is nan
    df.loc[df[income_col].isna(), output_col] = np.nan

    return df


def assign_representative_occupants(df):
    """representative value for 10+ is 11 according to 2019_5yrs_PUMS data in resstock-estimation"""
    df["rep_occupants"] = df["in.occupants"].replace("10+", "11").astype(int)

    return df


def read_file(file_path: Path):
    file_type = file_path.suffix
    if file_type == ".csv":
        return pd.read_csv(file_path, low_memory=False)
    elif file_type == ".parquet":
        return pd.read_parquet(file_path, low_memory=False)
    else:
        raise ValueError(f"file_type={file_type} not supported")


def write_to_file(df, file_path: Path):
    file_type = file_path.suffix
    if file_type == ".csv":
        df.to_csv(file_path, index=False)
    elif file_type == ".parquet":
        df.to_parquet(file_path)
    else:
        raise ValueError(f"file_type={file_type} not supported")

    print("File modified and saved in place of original file.")


def add_ami_column_to_file(file_path):
    print(
        "=============================================================================="
    )
    print(
        "This script is for use on ResStock results postprocessed for Sightglass only. "
    )
    print("Such as End Use Load Profile Round 1 results uploaded to OEDI (2022-09-16)")
    print(
        """
        https://data.openei.org/s3_viewer?bucket=oedi-data-lake&prefix=nrel-pds-building-
        stock%2Fend-use-load-profiles-for-us-building-stock%2F2022%2Fresstock_amy2018_
        release_1%2Fmetadata_and_annual_results%2Fnational%2Fcsv%2F
        """
    )
    print("The file expected has columns with prefixes such as: 'in.', 'out.'")
    print("")
    print(
        "This script requires resstock-estimation conda env and access to resstock-estimation GitHub repo"
    )
    print("to add 'in.area_median_income' column to file:")
    print(
        f"""
        {file_path}
        """
    )
    print(
        "=============================================================================="
    )
    file_path = Path(file_path)
    df = read_file(file_path)
    df = assign_representative_income(df)
    df = assign_representative_occupants(df)
    df = map_ami_by_income_limits_by_county(df)

    cols_to_drop = {"pct_ami", "rep_income", "rep_occupants"}
    cols_to_drop = list(cols_to_drop.intersection(set(df.columns)))
    df = df.drop(columns=cols_to_drop)
    write_to_file(df, file_path)


def main():
    """
    This
    Usage: python add_ami_to_euss_results.py path_to_euss_result_file.csv
    """
    if len(sys.argv) != 2:
        print("Usage: python add_ami_to_euss_results.py <path_to_euss_result_file.csv>")
        sys.exit(1)
    else:
        add_ami_column_to_file(sys.argv[1])


if __name__ == "__main__":
    main()
