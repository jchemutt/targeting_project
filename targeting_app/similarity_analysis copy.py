import os
import numpy as np
import pandas as pd
from scipy.spatial.distance import mahalanobis
from scipy.stats import zscore

def read_ascii(in_file):
    ascii_data = {}
    print(f"Reading: {in_file}")
    header_lines = []
    with open(in_file, 'r') as file:
        for _ in range(6):
            line = file.readline().strip()
            print(f"Header line: {line}")  # Debugging print
            header_lines.append(line)
    
    for line in header_lines:
        key, value = line.split()
        if key.lower() == 'nodata_value':
            try:
                ascii_data[key.lower()] = float(value)
            except ValueError:
                ascii_data[key.lower()] = -9999  # Default NODATA value if conversion fails
        else:
            try:
                ascii_data[key.lower()] = float(value)
            except ValueError:
                ascii_data[key.lower()] = value
    
    if 'nodata_value' not in ascii_data:
        print("NODATA_value not found in header, setting default -9999")
        ascii_data['nodata_value'] = -9999

    try:
        grid_data = np.loadtxt(in_file, skiprows=6)
        nodata_value = ascii_data.get('nodata_value', -9999)
        grid_data[grid_data == nodata_value] = np.nan
        ascii_data['grid_data'] = grid_data
    except ValueError as e:
        print(f"Error loading grid data: {e}")
        raise

    return ascii_data

def write_ascii(out_file, ascii_data):
    no_data = -9999
    with open(out_file, 'w') as file:
        for key, value in ascii_data.items():
            if key == 'grid_data':
                continue
            if key == 'nodata_value':
                file.write(f"NODATA_value  {no_data}\n")
            else:
                file.write(f"{key}         {value}\n")

        grid_data = ascii_data['grid_data']
        grid_data[np.isnan(grid_data)] = no_data
        np.savetxt(file, grid_data, fmt='%.6f')
    print(f"Saving: {out_file}")

def calculate_mahalanobis(threshold, idx, total_files, out_folder, df, ascii_data):
    mean_vec = threshold.iloc[:, idx:idx + total_files].mean().values
    print(f"Mean vector: {mean_vec}")

    # Remove rows with NaN values for covariance calculation
    df_clean = df[~np.isnan(df).any(axis=1)]
    
    if df_clean.shape[0] > 1:
        cov_matrix = np.cov(df_clean, rowvar=False)
    else:
        print("Not enough data to calculate covariance matrix, using identity matrix.")
        cov_matrix = np.eye(df_clean.shape[1])
    
    print(f"Covariance matrix: {cov_matrix}")

    try:
        inv_cov_matrix = np.linalg.pinv(cov_matrix)
        print(f"Inverse covariance matrix: {inv_cov_matrix}")

        mahal_dist = np.array([mahalanobis(row, mean_vec, inv_cov_matrix) for row in df])
        ascii_data['grid_data'] = mahal_dist.reshape(int(ascii_data['nrows']), int(ascii_data['ncols']))
        write_ascii(os.path.join(out_folder, "MahalanobisDist.asc"), ascii_data)
        print("Mahalanobis Distance calculated")
    except Exception as e:
        print(f"Error in calculate_mahalanobis: {e}")

def calculate_mess(df, total_files, threshold, idx, ascii_data, out_folder):
    print("Calculating MESS")
    bool_na = ~np.isnan(df).any(axis=1)
    mess_result = np.full(df.shape[0], np.nan)
    for i in range(df.shape[0]):
        if bool_na[i]:
            z = zscore(df[i, :])
            mess_result[i] = min(z)
    ascii_data['grid_data'] = mess_result.reshape(int(ascii_data['nrows']), int(ascii_data['ncols']))
    write_ascii(os.path.join(out_folder, "MESS.asc"), ascii_data)
    print("MESS calculated")

def similarity_analysis(total_files, work_space):
    df = pd.DataFrame()
    ascii_data = None
    threshold = pd.read_csv(os.path.join(work_space, "temp.csv"))

    for i in range(1, total_files + 1):
        ascii_data = read_ascii(os.path.join(work_space, f"tempascii_{i}.asc"))
        if i == 1:
            df = pd.DataFrame(ascii_data['grid_data'].reshape(-1, 1))
        else:
            df = pd.concat([df, pd.DataFrame(ascii_data['grid_data'].reshape(-1, 1))], axis=1)

    try:
        calculate_mahalanobis(threshold, 0, total_files, work_space, df.values, ascii_data)
    except Exception as e:
        print(f"Error in calculate_mahalanobis: {e}")

    try:
        calculate_mess(df.values, total_files, threshold, 0, ascii_data, work_space)
    except Exception as e:
        print(f"Error in calculate_mess: {e}")
