import os
import pandas as pd
import yaml
from functools import reduce
import pathlib
from ast import literal_eval
import plotly
import plotly.express as px
import plotly.figure_factory as ff
import sqlalchemy as sa
import numpy as np
import csv

from eulpda.smart_query.resstock_athena import ResStockAthena
from eulpda.smart_query.eulp_athena import EULPAthena
from eulpda.smart_query.resstock_savings import ResStockSavings

class ZonalHeatPump():
  def __init__(self, enduses, group_by):
    self.enduses = enduses
    self.group_by = group_by
    self.get_query_only = False


  def get_resstock_savings(self):
    sample_weight = 136569411.0 / 550000.0 # total downselected samples
    print('sample_weight: {}'.format(sample_weight))

    resstock_savings = ResStockSavings(workgroup='zonalhp',
                                       db_name='zonal-hp',
                                       buildstock_type='resstock',
                                       table_name='final001',
                                       sample_weight=sample_weight,
                                       skip_reports=True)
    return resstock_savings

  def get_savings_shape(self, upgrade_id, upgrade_name):
    df = resstock_savings.savings_shape(upgrade_id=upgrade_id,
                                        enduses=self.enduses,
                                        group_by=self.group_by,
                                        annual_only=True,
                                        applied_only=True,
                                        get_query_only=self.get_query_only)

    df['upgrade_name'] = upgrade_name

    for col in self.enduses:
      df[f'{col}__average_savings'] = df[f'{col}__savings'] / df['units_count']

    return df


  def get_results_csv(self, upgrade_id=None):
    if upgrade_id == None:
      df = resstock_savings.get_results_csv()
    else:
      df = resstock_savings.get_upgrades_csv(upgrade=upgrade_id)
    
    return df


def stacked_bar(df, enduses, group_by):
  for group in group_by:
    for enduse in enduses:
      fig = px.histogram(df, x=group, y=f'{enduse}__savings', color='upgrade_name', barmode='group',
                         title=f'Total annual savings for {enduse}')

      path = os.path.join(os.path.dirname(__file__), f'upgrade_{group}_{enduse}.html')
      plotly.offline.plot(fig, filename=path, auto_open=False)

      fig = px.histogram(df, x=group, y=f'{enduse}__average_savings', color='upgrade_name', barmode='group',
                         title=f'Average annual savings for {enduse}')

      path = os.path.join(os.path.dirname(__file__), f'upgrade_average_{group}_{enduse}.html')
      plotly.offline.plot(fig, filename=path, auto_open=False)


def histogram(baseline, up, enduses, group_by):
  df = baseline[enduses].subtract(up[enduses])
  df = baseline[group_by].join(df)

  for group in group_by:
    for enduse in enduses:
      fig = px.histogram(baseline, x=enduse, color=group,
                         marginal='box')
      path = os.path.join(os.path.dirname(__file__), f'histogram_{group}_{enduse}.html')
      plotly.offline.plot(fig, filename=path, auto_open=False)


def density(baseline, up, enduses, group_by):
  df = baseline[enduses].subtract(up[enduses])
  df = baseline[group_by].join(df)

  for group in group_by:
    for enduse in enduses:
      hist_data = []
      group_labels = []

      for item in df[group].unique():
        sub = df.copy()
        sub = sub[sub[group]==item]

        hist_data.append(sub[enduse].values)
        group_labels.append(item)

      fig = ff.create_distplot(hist_data, group_labels, bin_size=1, show_hist=False, show_curve=True, show_rug=True)
      path = os.path.join(os.path.dirname(__file__), f'density_{group}_{enduse}.html')
      plotly.offline.plot(fig, filename=path, auto_open=False)


def value_counts(df, file):
  value_counts = []
  with open(file, 'w', newline='') as f:
    for col in sorted(df.columns):
      value_count = df[col].value_counts(normalize=True)
      value_count = value_count.round(2)
      keys_to_values = dict(zip(value_count.index.values, value_count.values))
      keys_to_values = dict(sorted(keys_to_values.items(), key=lambda x: (x[1], x[0]), reverse=True))
      value_counts.append([value_count.name])
      value_counts.append(keys_to_values.keys())
      value_counts.append(keys_to_values.values())
      value_counts.append('')

    w = csv.writer(f)
    w.writerows(value_counts)


if __name__ == '__main__':

  enduses = ['energy_use_total_m_btu']
  group_by = ['ashrae_iecc_climate_zone_2004', 'geometry_building_type_recs']
  upgrades = {
                # 1: 'Envelope Only',
                2: 'Envelope And MSHP (R-30)',
                3: 'Envelope And MSHP (R-5)',
                4: 'Envelope And MSHP (R-15)'
             }




  # stacked bars comparing across upgrades
  path = os.path.join(os.path.dirname(__file__), 'zonalhp.csv')
  if not os.path.exists(path):
    zonal_heat_pump = ZonalHeatPump(enduses, group_by)
    resstock_savings = zonal_heat_pump.get_resstock_savings()

    dfs = {}
    for upgrade_id, upgrade_name in upgrades.items():
      df = zonal_heat_pump.get_savings_shape(upgrade_id, upgrade_name)
      dfs[upgrade_id] = df

    df = pd.concat(dfs)
    df.to_csv(path, index=False)
  else:
    df = pd.read_csv(path)

  stacked_bar(df, enduses, group_by)




  
  # comparing across characteristics
  enduses = [
              'report_simulation_output.energy_use_total_m_btu',
              'report_simulation_output.fuel_use_electricity_total_m_btu',
              'report_simulation_output.fuel_use_natural_gas_total_m_btu'
            ]
  group_by = [
                'build_existing_model.geometry_building_type_recs',
                'build_existing_model.hvac_heating_type',
                'build_existing_model.hvac_heating_efficiency',
                'build_existing_model.hvac_cooling_efficiency'
             ]

  path = os.path.join(os.path.dirname(__file__), 'results_up0.csv')
  if not os.path.exists(path):
    zonal_heat_pump = ZonalHeatPump(enduses, group_by)
    resstock_savings = zonal_heat_pump.get_resstock_savings()

    baseline = zonal_heat_pump.get_results_csv()
    baseline.to_csv(path)

    ups = {}
    for upgrade_id in upgrades.keys():
      path = os.path.join(os.path.dirname(__file__), f'results_up{upgrade_id}.csv')
      df = zonal_heat_pump.get_results_csv(upgrade_id)
      df.to_csv(path)
      ups[upgrade_id] = df
  else:
    baseline = pd.read_csv(path)

    ups = {}
    for upgrade_id in upgrades.keys():
      path = os.path.join(os.path.dirname(__file__), f'results_up{upgrade_id}.csv')
      ups[upgrade_id] = pd.read_csv(path)

  baseline = baseline[baseline['completed_status']=='Success']
  baseline = baseline.set_index('building_id').sort_index()
  baseline = baseline[enduses + group_by]

  for upgrade_id, up in ups.items():
    up = up[up['completed_status']=='Success']
    up = up.set_index('building_id').sort_index()
    up = up[enduses]
    ups[upgrade_id] = up

  for upgrade_id, up in ups.items():
    if upgrade_id != 4:
      continue

    histogram(baseline, up, enduses, group_by) # stacked histograms
    density(baseline, up, enduses, group_by) # histograms with density lines





  # summary statistics for group_by
  path = os.path.join(os.path.dirname(__file__), 'value_counts.csv')
  value_counts(baseline[group_by], path)
