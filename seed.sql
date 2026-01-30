insert into companies (name, cui, invoice_series)
values
  ('ROSIPROD SRL', 'RO9608452', 'ROS'),
  ('SILAI CEREAL COMPANY SRL', 'RO45698419', 'SCC'),
  ('OUAI DOLOSMANU', 'RO41291160', 'OD')
on conflict (cui) do nothing;
