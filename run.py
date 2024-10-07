import os
import subprocess
import sys
import time
import argparse
from yaspin import yaspin
from yaspin.spinners import Spinners

PYTHON_INSTRUCTIONS = """ 
Download here ⬇️
https://www.python.org/downloads/
"""

NODEJS_INSTRUCTIONS = """ 
Download here ⬇️
https://nodejs.org/en/download 
"""

def error(spinner, message):
    spinner.fail("❌")
    print(f"Error: {message}", file=sys.stderr)
    sys.exit(1)

def run_command(command, check=False):
    result = subprocess.run(command, shell=True, capture_output=True, text=True)
    
    if result.returncode != 0:
        if check:
            raise subprocess.CalledProcessError(
                result.returncode, command, output=result.stdout, stderr=result.stderr
            )
        return None

    return result.stdout.strip()

def check_python_installed():
    with yaspin(text="Checking Python dependency...") as spinner:
        version_check = run_command("python3 --version")
        if version_check is None:
            error(spinner, f"Python 3.11+ is required for quickstart. {PYTHON_INSTRUCTIONS}")
        try:
            version_parts = version_check.strip("Python").split('.')
            minor_version = int(version_parts[1])
            if minor_version < 11:
                error(spinner, f"Python 3.11+ is required for quickstart. Found version: {version_check.strip()} {PYTHON_INSTRUCTIONS}")
        except (IndexError, ValueError):
            error(spinner, f"Python 3.11+ is required for quickstart. Unable to determine Python version: {version_check.strip()} {PYTHON_INSTRUCTIONS}")
        spinner.ok("✅")
        spinner.write(f"Supported version found: {version_check.strip()}")

def check_js_installed():
    with yaspin(text="Checking JavaScript dependencies...") as spinner:
        node_check = run_command("node -v")
        npm_check = run_command("npm -v")
        if node_check is None or npm_check is None:
            error(spinner, f"Node.js and npm must be installed for quickstart. {NODEJS_INSTRUCTIONS}")
        spinner.ok("✅")
        spinner.write(f"Supported Node.js version found: {node_check.strip()}")
        spinner.write(f"Supported npm version found: {npm_check.strip()}")

def create_project(project_name):
    with yaspin(text=f"Creating project {project_name}...") as spinner:
        try:
            run_command(f"diagrid project create {project_name} --deploy-managed-kv --deploy-managed-pubsub --enable-managed-workflow", check=True)
            spinner.ok("✅")
        except subprocess.CalledProcessError as e:
            spinner.fail("❌")
            if e.output:
                spinner.write(f"Error: {e.output}")
            if e.stderr:
                spinner.write(f"Error: {e.stderr}")
            sys.exit(1)


def create_appid(project_name, appid_name):
    with yaspin(text=f"Creating App ID {appid_name}...") as spinner:
        try:
            run_command(f"diagrid appid create -p {project_name} {appid_name}",check=True)
            spinner.ok("✅")
        except subprocess.CalledProcessError as e:
            spinner.fail(f"❌")
            if e.output:
                spinner.write(f"Error: {e.output}")
            if e.stderr:
                spinner.write(f"Error: {e.stderr}")
            sys.exit(1)

def check_appid_status(project_name, appid_name):
    max_attempts = 8
    attempt = 1
    last_status = None
    
    with yaspin(text=f"Waiting for App ID {appid_name} to become ready. This may take 1-2 minutes...", timer=True) as spinner:
        while attempt <= max_attempts:
            status_output = run_command(f"diagrid appid get {appid_name} -p {project_name}")

            status_lines = status_output.split('\n')
            status = None
            for line in status_lines:
                if 'Status:' in line:
                    status = line.split('Status:')[1].strip()
                    last_status = status
                    break

            if status and (status.lower() == "ready" or status.lower() == "available"):
                spinner.ok("✅")
                return 

            time.sleep(10)
            attempt += 1

        spinner.fail("❌")
        spinner.write(f"App ID {appid_name} is still provisioning")
        spinner.write(f"Run `diagrid appid get {appid_name} --project {project_name}` and proceed with quickstart once in ready status")
        sys.exit(1)

def create_subscription(project_name, subscription_name, topic_name, scopes, route):
    with yaspin(text=f"Creating subscription {subscription_name}...") as spinner:
        try:
            run_command(f"diagrid subscription create {subscription_name} --component pubsub --topic {topic_name} --scopes {scopes} --route {route} --project {project_name}", check=True)
            spinner.ok("✅")
        except subprocess.CalledProcessError as e:
            spinner.fail("❌")
            if e.output:
                spinner.write(f"{e.output}")
            if e.stderr:
                spinner.write(f"{e.stderr}")
            sys.exit(1)

def create_binding(binding_name):
    with yaspin(text=f"Creating binding {binding_name}...") as spinner:
        try:
            run_command(f"diagrid components apply -f ./catalyst-resources/square-http-binding.yaml", check=True)
            spinner.ok("✅")
        except subprocess.CalledProcessError as e:
            spinner.fail("❌")
            if e.output:
                spinner.write(f"{e.output}")
            if e.stderr:
                spinner.write(f"{e.stderr}")
            sys.exit(1)

def set_default_project(project_name):
    with yaspin(text=f"Setting default project to {project_name}...") as spinner:
        try:
            run_command(f"diagrid project use {project_name}", check=True)
            spinner.ok("✅")
        except subprocess.CalledProcessError as e:
            spinner.fail("❌")
            spinner.write("Failed to set default project")
            if e.output:
                spinner.write(f"{e.output}")
            if e.stderr:
                spinner.write(f"{e.stderr}")
            sys.exit(1)

def scaffold_and_update_config(config_file):
    with yaspin(text="Preparing dev config file...") as spinner:
        scaffold_output = run_command("diagrid dev scaffold", check=True)
        if scaffold_output is None:
            error(spinner, "Failed to prepare dev config file")

        # Create and activate a virtual environment
        env_name = "diagrid-venv"
        if os.path.exists(env_name):
            # spinner.write(f"Existing virtual environment found: {env_name}")
            # spinner.write(f"Deleting existing virtual environment: {env_name}")
            run_command(f"rm -rf {env_name}", check=True)

        # spinner.write(f"Creating virtual environment: {env_name}")
        run_command(f"python3 -m venv {env_name}", check=True)

        # spinner.write(f"Installing pyyaml in the virtual environment: {env_name}")
        run_command(f"./{env_name}/bin/pip install pyyaml", check=True)

        # Run the Python script to update the dev config file
        # spinner.write("Updating dev config file...")
        run_command(f"./{env_name}/bin/python scaffold.py", check=True)
        spinner.ok("✅")

def main():
    prj_name = os.getenv('WORKFLOW_PROJECT_NAME')

    config_file_name = f"dev-{prj_name}.yaml"
    os.environ['CONFIG_FILE'] = config_file_name

    parser = argparse.ArgumentParser(description="Run the setup script for Diagrid projects.")
    parser.add_argument('--project-name', type=str, default=prj_name,
                        help="The name of the project to create/use.")
    parser.add_argument('--config-file', type=str, default=config_file_name,
                       help="The name of the config file to scaffold and use.")
    args = parser.parse_args()

    project_name = args.project_name
    config_file = args.config_file

    check_python_installed()
    check_js_installed()

    create_project(prj_name)

    set_default_project(prj_name)

    create_appid(prj_name, "inventory")
    create_appid(prj_name, "notify")
    create_appid(prj_name, "order-processor")
    create_appid(prj_name, "payments")
    create_appid(prj_name, "shipping")

    check_appid_status(prj_name, "inventory")
    check_appid_status(prj_name, "notify")
    check_appid_status(prj_name, "order-processor")
    check_appid_status(prj_name, "payments")
    check_appid_status(prj_name, "shipping")

    # Create pub/sub subscription 
    create_subscription(prj_name, "notifications-sub", "notifications", "notify", "/notifications")

    # Create http output binding 
    create_binding("square-api")

    # Check if the dev file already exists and remove it if it does
    if os.path.isfile(config_file):
        print(f"Existing dev config file found: {config_file}")
        try:
            os.remove(config_file)
            print(f"Deleted existing config file: {config_file}")
        except Exception as e:
            with yaspin(text=f"Error deleting file {config_file}") as spinner:
                error(spinner, f"Error deleting file {config_file}: {e}")

    scaffold_and_update_config(config_file)


if __name__ == "__main__":
    main()

