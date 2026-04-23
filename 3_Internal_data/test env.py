# from dotenv import load_dotenv
# import os
# # MUST be called before os.environ.get()
# load_dotenv()
#
# # CONFIGURATION
# HF_TOKEN: str = os.environ.get("HF_TOKEN", "")
# print(f"HF_TOKEN: {HF_TOKEN}")

# import os
#
# # To remove it from the current session
# if "HF_TOKEN" in os.environ:
#     del os.environ["HF_TOKEN"]
#     print("HF_TOKEN has been removed from the environment.")

import os
import dotenv

HF_TOKEN: str = os.environ.get("HF_TOKEN", "")
print(f"HF_TOKEN: {HF_TOKEN}")
