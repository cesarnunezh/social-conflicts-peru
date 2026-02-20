import pandas as pd
from pathlib import Path
from ..config import directories

FILE_PATH = directories.RAW_DATA / "bdConflictosMineros.xlsx"

def load_data(file_path: Path) -> pd.DataFrame:
    return pd.read_excel(file_path)

def save_description(df: pd.DataFrame, file_name: str = "description.json"):
    # Keeps only the descriptions of the conflict
    desc_df = df.iloc[:,0:3]
    desc_df.to_json(directories.PROCESSED_DATA / file_name)
    return None

def filter_cols(df: pd.DataFrame, pattern: str):
    col_filter = [col for col in df.columns if col.startswith(pattern) or col == 'id']
    return df[col_filter]

def save_expressions(df: pd.DataFrame, file_name: str = "expressions.json"):
    
    # Keep a data for dates, and a data for details
    df_exp1 = filter_cols(df, 'd_Expresión')
    df_exp2 = filter_cols(df, 'InfoExpresión')

    # Transforming the data into tidy format
    df_exp1 = pd.melt(df_exp1, id_vars= ['id'], var_name='dateExpresión', value_name='d_Expresión').reset_index()
    df_exp2 = pd.melt(df_exp2, id_vars= ['id'], var_name='infoExpresión', value_name='InfoExpresión').reset_index()

    # Merge date and details dataframes
    df_exp = pd.merge(df_exp1[["id", "index", "d_Expresión"]], df_exp2[["id", "index", "InfoExpresión"]], on=['index','id'])
    del df_exp["index"]

    # Droping missing values
    df_exp = df_exp[df_exp[['InfoExpresión','d_Expresión']].notna().all(axis=1)]

    # Exporting data to jsonfile
    df_exp.to_json(directories.PROCESSED_DATA / file_name)

def transform_df_status(df: pd.DataFrame, active_status: set = {"nuevo", "reactivado"} ):

    # Transforming date 
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["id", "date"])

    # Generate is_active variable
    df["is_active"] = df["status"].isin(active_status)

    # Generate a dataset with previous vs post status track
    changed = df.groupby("id")["is_active"].shift().ne(df["is_active"])
    df["spell_id"] = changed.groupby(df["id"]).cumsum()

    # 4) summarize each spell (start is first date in spell)
    spells = (
        df.groupby(["id", "spell_id"], as_index=False)
        .agg(
            start_date=("date", "min"),
            end_marker=("date", "max"),  
            is_active=("is_active", "first"),
        )
    )

    spells["end_date"] = spells.groupby("id")["start_date"].shift(-1)

    # Changing variables for final output
    spells["status"] = spells["is_active"].map({True: "active", False: "non_active"})
    final_date = pd.Timestamp(2022, 7, 1)
    df_periods = spells.loc[:, ["id", "status", "start_date", "end_date"]]
    df_periods["end_date"] = df_periods["end_date"].fillna(final_date)

    return df_periods


def save_status(df: pd.DataFrame, file_name: str = "status.json"):

    df_status = filter_cols(df, "d_TC")

    df_status_long = df_status.melt(id_vars = "id", var_name = "status", value_name="date").dropna(subset=["date"])

    mapping = {
        r"d_TCNuevo": "nuevo",
        r"d_TCLatente\d*": "latente",
        r"d_TCReactivado\d*": "reactivado",
        r"d_TCResuelto": "resuelto",
        r"d_TCRetirado\d*": "retirado",
    }

    df_status_long["status"] = df_status_long["status"].replace(mapping, regex=True)
    df_status_long = df_status_long.sort_values(["id", "date"])

    df_status_final = transform_df_status(df_status_long)
    df_status_final.to_json(directories.PROCESSED_DATA / file_name)

if __name__ == "__main__":
    
    df = load_data(FILE_PATH)
    save_description(df)
    save_expressions(df)
    save_status(df)    