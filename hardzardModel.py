import pandas as pd
from xgboost import XGBRegressor
from sklearn.model_selection import train_test_split

# Load the datasets
# Update the filenames to match exactly what you downloaded
power_plants = pd.read_csv('data/eia_860m_plants.csv')
weather_events = pd.read_csv('data/noaa_storm_events.csv')

# Print the first few rows to verify they loaded correctly
print(power_plants.head())
print(weather_events.head())