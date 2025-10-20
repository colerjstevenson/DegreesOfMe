
import sys
import asyncio
import aiohttp
from PyQt5.QtWidgets import (
    QApplication, QWidget, QLabel, QLineEdit,
    QPushButton, QVBoxLayout, QHBoxLayout,
    QListWidget, QListWidgetItem, QTextEdit, QMessageBox
)
from PyQt5.QtCore import Qt

# Assume your connector backend module is imported:
from DegreesOfMe3 import find_actor_to_any_goal_async

class ActorMovieApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Actor → Movie Connection Finder")

        # self.goal_titles = [
        #     "The Bad Guys 2", "Ghostbusters: Frozen Empire"
        # ]
        self.goal_titles = ["They Who Surround US"]
        self.excluded_ids = set()
        self.excluded_titles_by_id = {}  # map id → title
        self.current_actor = None

        self.initUI()

    def initUI(self):
        layout = QVBoxLayout()

        # Actor input
        actor_layout = QHBoxLayout()
        actor_label = QLabel("Actor Name:")
        self.actor_edit = QLineEdit()
        actor_layout.addWidget(actor_label)
        actor_layout.addWidget(self.actor_edit)
        layout.addLayout(actor_layout)

        # Excluded list display + Clear button
        excl_label = QLabel("Excluded Film IDs (ID — Title):")
        self.excl_display = QTextEdit()
        self.excl_display.setReadOnly(True)
        clear_btn = QPushButton("Clear Exclude List")
        clear_btn.clicked.connect(self.on_clear_excludes)

        layout.addWidget(excl_label)
        layout.addWidget(self.excl_display)
        layout.addWidget(clear_btn)

        # Find button
        self.btn_find = QPushButton("Find Connection")
        self.btn_find.clicked.connect(self.on_find)
        layout.addWidget(self.btn_find)

        # Path results list (clickable)
        path_label = QLabel("Result Path (click MOVIE to exclude its ID and retry):")
        self.path_list = QListWidget()
        self.path_list.itemClicked.connect(self.on_path_item_clicked)
        layout.addWidget(path_label)
        layout.addWidget(self.path_list)

        # Status label
        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        self.setLayout(layout)
        self.resize(600, 500)

    def update_excluded_display(self):
        lines = []
        for mid in sorted(self.excluded_ids):
            title = self.excluded_titles_by_id.get(mid, "(unknown title)")
            lines.append(f"{mid} — {title}")
        self.excl_display.setPlainText("\n".join(lines))

    def on_clear_excludes(self):
        self.excluded_ids.clear()
        self.excluded_titles_by_id.clear()
        self.update_excluded_display()
        self.status_label.setText("Exclude list cleared.")
        # Optionally clear previous path results
        self.path_list.clear()

    def on_find(self):
        actor_name = self.actor_edit.text().strip()
        if not actor_name:
            QMessageBox.warning(self, "Input Error", "Please enter an actor name.")
            return

        self.current_actor = actor_name
        self.status_label.setText("Searching… please wait.")
        self.btn_find.setEnabled(False)
        self.path_list.clear()
        self.update_excluded_display()

        # Run async search
        named_path, actor_clean = asyncio.run(
            find_actor_to_any_goal_async(actor_name,
                                         self.goal_titles,
                                         self.excluded_ids)
        )
        self.btn_find.setEnabled(True)

        if named_path:
            self.status_label.setText(f"Found path for actor {actor_clean}. Click a movie to exclude and retry.")
            self.populate_path_list(named_path)
        else:
            self.status_label.setText(f"No connection found for actor {actor_clean} (with exclusions).")

    def populate_path_list(self, named_path):
        self.path_list.clear()
        for ntype, nm, nid in named_path:
            label = f"{ntype.upper()}: {nm} (ID {nid})"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, (ntype, nm, nid))
            self.path_list.addItem(item)

    def on_path_item_clicked(self, item):
        data = item.data(Qt.UserRole)
        if not data:
            return
        ntype, name, nid = data
        if ntype == "movie":
            ret = QMessageBox.question(
                self,
                "Exclude Film",
                f"Exclude movie '{name}' (ID {nid}) and retry?",
                QMessageBox.Yes | QMessageBox.No
            )
            if ret == QMessageBox.Yes:
                # Add to exclude list
                self.excluded_ids.add(nid)
                self.excluded_titles_by_id[nid] = name
                self.update_excluded_display()

                # Re-run search
                self.status_label.setText(f"Excluding ID {nid} — {name}, re-searching…")
                self.btn_find.setEnabled(False)
                self.path_list.clear()

                named_path, actor_clean = asyncio.run(
                    find_actor_to_any_goal_async(self.current_actor,
                                                 self.goal_titles,
                                                 self.excluded_ids)
                )
                self.btn_find.setEnabled(True)

                if named_path:
                    self.status_label.setText("Found new path after exclusion.")
                    self.populate_path_list(named_path)
                else:
                    self.status_label.setText(f"No connection found with exclusions: {self.excluded_ids}")
        else:
            # Actor clicked — do nothing or show info
            pass

def main():
    app = QApplication(sys.argv)
    w = ActorMovieApp()
    w.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
