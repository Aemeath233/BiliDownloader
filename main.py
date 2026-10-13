import time

STARTED_AT = time.perf_counter()

from bili_client.app import main

if __name__ == "__main__":
    main(started_at=STARTED_AT)
