import os
import yaml

config_file = os.getenv('CONFIG_FILE')

with open(config_file, 'r') as file:
    config_data = yaml.load(file, Loader=yaml.FullLoader)
    print(config_data)

for app in config_data['apps']:
    if app['appId'] == 'inventory':
        app['appPort'] = 3002
        app['workDir'] = './services/inventory'
        app['command'] = ['python3', 'app.py']
    elif app['appId'] == 'notify':
        app['appPort'] = 3001
        app['workDir'] = './services/notifications'
        app['command'] = ['python3', 'app.py']
    elif app['appId'] == 'order-processor':
        app['appPort'] = 3000
        app['workDir'] = './services/order-processor'
        app['command'] = ['python3', 'app.py']
    elif app['appId'] == 'payments':
        app['appPort'] = 3003
        app['workDir'] = './services/payments'
        app['command'] = ['python3', 'app.py']
    elif app['appId'] == 'shipping':
        app['appPort'] = 3004
        app['workDir'] = './services/shipping'
        app['command'] = ['python3', 'app.py']
    


updated_data = {
    'project': config_data['project'],
    'apps': config_data['apps'],
    'appLogDestination': config_data.get('appLogDestination', '')
}

with open(config_file, 'w') as file:
    yaml.safe_dump(updated_data, file, default_flow_style=False, sort_keys=False)

print("Dev config file has been updated")


