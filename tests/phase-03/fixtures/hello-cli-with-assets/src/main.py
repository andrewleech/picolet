import os
print("Hello from hello-cli-with-assets")
# Verify the asset file is accessible in the romfs.
with open("/rom/assets/data.txt") as f:
    print("asset:", f.read().strip())
