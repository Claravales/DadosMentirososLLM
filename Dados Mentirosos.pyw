# Doble clic para abrir Dados Mentirosos (sin ventana de terminal).
import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.getcwd())

from web_app import main

sys.exit(main())
