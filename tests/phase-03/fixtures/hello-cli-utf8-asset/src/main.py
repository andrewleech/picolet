import os
# List the data directory to confirm UTF-8 filenames survive the romfs round-trip.
files = sorted(os.listdir("/rom/data"))
print("utf8-files:", ",".join(files))
