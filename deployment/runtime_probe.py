import struct
import sys
import xlrd
import openpyxl
import et_xmlfile


def main():
    versions = ((xlrd.__version__, "2.0.2"), (openpyxl.__version__, "3.1.5"), (et_xmlfile.__version__, "2.0.0"))
    if struct.calcsize("P") != 8 or any(actual != expected for actual, expected in versions):
        raise SystemExit("运行包版本或位数不符")
    print(sys.version.split()[0])


if __name__ == "__main__":
    main()
