"""pinside -- turn a KiCad board into the geometry and the go/no-go a test fixture needs.

    from pinside import read_board, transform, run
    board = transform(read_board("board.kicad_pcb"), mirror="x")
    for finding in run(board):
        print(finding)
"""

from .board import Board, MountingHole, TestPoint, read_board, transform
from .checks import ERROR, INFO, WARNING, Finding, Limits, run

__version__ = "0.1.0"
__all__ = ["Board", "TestPoint", "MountingHole", "read_board", "transform",
           "Finding", "Limits", "run", "ERROR", "WARNING", "INFO", "__version__"]
