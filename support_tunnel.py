from libs.device_binding import enforce_mac_binding
from libs.support_tunnel import main


if __name__ == "__main__":
    if not enforce_mac_binding():
        raise SystemExit(1)

    raise SystemExit(main())
