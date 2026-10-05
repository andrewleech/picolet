import subprocess


def run_with_shell(command: str) -> None:
    subprocess.run(command, shell=True, check=False)
