print("Hello from hello-cli-multi-include")
# Verify both include directories and nested subdirectory are in romfs.
with open("/rom/assets/readme.txt") as f:
    print("assets:", f.read().strip())
with open("/rom/config/settings.txt") as f:
    print("config:", f.read().strip())
with open("/rom/assets/images/icon.png") as f:
    print("nested:", f.read().strip())
