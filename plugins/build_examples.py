"""Build the examples from the Cantera repository into Nikola listings.

This plugin finds the Cantera examples directory in the Cantera repository
to process the examples into nicely formatted HTML along the lines of
Nikola listings. The Cantera examples are found in the ``EXAMPLES_FOLDERS``
configuration option from the top-level config.py.
"""
from pathlib import Path
import ast
import re
import base64
import mimetypes

from collections import OrderedDict
import os
import lxml.html
import json

from nikola.plugin_categories import Task
from nikola import utils

from pygments import highlight
from pygments.util import ClassNotFound
from pygments.lexers import get_lexer_for_filename, guess_lexer, TextLexer, MatlabLexer
import natsort


class BuildExamples(Task):
    """Build the Cantera examples into the documentation."""

    name = "build_examples"

    def set_site(self, site):
        """Set Nikola site."""
        self.kw = {
            "default_lang": site.config["DEFAULT_LANG"],
            "examples_folders": site.config["EXAMPLES_FOLDERS"],
            "output_folder": Path(site.config["OUTPUT_FOLDER"]),
            "index_file": site.config["INDEX_FILE"],
            "strip_indexes": site.config["STRIP_INDEXES"],
            "cache_folder": site.config["CACHE_FOLDER"],
        }

        # Verify that no folder in EXAMPLES_FOLDERS appears twice (on output side)
        appearing_paths = set()
        for source, dest in self.kw["examples_folders"].items():
            if source in appearing_paths or dest in appearing_paths:
                problem = source if source in appearing_paths else dest
                utils.LOGGER.error(
                    "The examples input or output folder '{0}' appears in more "
                    "than one entry in EXAMPLES_FOLDERS, exiting.".format(problem)
                )
                continue
            appearing_paths.add(source)
            appearing_paths.add(dest)

        return super(BuildExamples, self).set_site(site)

    def gen_tasks(self):
        """Render examples."""
        # Things to ignore in examples
        self.ignored_extensions = (".pyc", ".pyo", ".cti", ".dat", ".ipynb_checkpoints")

        def render_example_index(
            ex_type, headers, input_folder, output_folder, output_file
        ):
            def chunks(l, n):
                """Yield successive n-sized chunks from l.

                https://stackoverflow.com/a/312464
                """
                for i in range(0, len(l), n):
                    yield l[i : i + n]

            for head, file_dict in headers.items():
                file_dict["files"] = list(chunks(file_dict["files"], 3))

            permalink = output_file.relative_to(self.kw["output_folder"])
            title = "{} Examples".format(ex_type).title()
            context = {
                "headers": headers,
                "lang": self.kw["default_lang"],
                "pagekind": ["example"],
                "permalink": str(permalink),
                "title": title,
                "description": title,
            }
            self.site.render_template(
                "{}-example-index.tmpl".format(ex_type), str(output_file), context
            )

        def render_example(in_name, out_name, input_folder, output_folder):
            needs_ipython_css = False
            if in_name.suffix == ".ipynb":
                # Special handling: render ipynb in listings (Issue #1900)
                ipynb_compiler = self.site.plugin_manager.getPluginByName(
                    "ipynb", "PageCompiler"
                ).plugin_object
                with in_name.open(mode="r") as in_file:
                    nb_json = ipynb_compiler._nbformat_read(in_file)
                ipynb_raw = ipynb_compiler._compile_string(nb_json)
                ipynb_html = lxml.html.fromstring(ipynb_raw)
                code = lxml.html.tostring(ipynb_html, encoding="unicode")
                needs_ipython_css = True
            elif in_name.suffix == ".m":
                lexer = MatlabLexer()
                code = highlight(
                    in_name.read_bytes(), lexer, utils.NikolaPygmentsHTML(in_name.name)
                )
            else:
                try:
                    lexer = get_lexer_for_filename(in_name.name)
                except ClassNotFound:
                    try:
                        lexer = guess_lexer(in_name.read_bytes())
                    except ClassNotFound:
                        lexer = TextLexer()
                code = highlight(
                    in_name.read_bytes(), lexer, utils.NikolaPygmentsHTML(in_name.name)
                )

            title = in_name.name

            permalink = out_name.relative_to(self.kw["output_folder"])
            source_link = permalink.stem  # remove '.html'
            context = {
                "code": code,
                "title": title,
                "permalink": str(permalink),
                "lang": self.kw["default_lang"],
                "description": title,
                "source_link": source_link,
                "pagekind": ["example"],
            }
            if needs_ipython_css:
                # If someone does not have ipynb posts and only listings, we
                # need to enable ipynb CSS for ipynb listings.
                context["needs_ipython_css"] = True
            self.site.render_template("examples.tmpl", str(out_name), context)

        yield self.group_task()

        # When any key or value in the uptodate dictionary changes, the
        # examples pages need to be rebuilt.
        uptodate = {"c": self.site.GLOBAL_CONTEXT}

        for k, v in self.site.GLOBAL_CONTEXT["template_hooks"].items():
            uptodate["||template_hooks|{0}||".format(k)] = v.calculate_deps()

        for k in self.site._GLOBAL_CONTEXT_TRANSLATABLE:
            uptodate[k] = self.site.GLOBAL_CONTEXT[k](self.kw["default_lang"])

        # save navigation links as dependencies
        uptodate["nav_links"] = uptodate["c"]["navigation_links"](
            self.kw["default_lang"]
        )

        uptodate["kw"] = self.kw

        for input_folder, example_folder in self.kw["examples_folders"].items():

            #########################################################
            # Build the Python examples
            #########################################################
            if "python" in example_folder:
                index_template_deps = self.site.template_system.template_deps(
                    "python-example-index.tmpl"
                )
                examples_template_deps = self.site.template_system.template_deps(
                    "examples.tmpl"
                )
                headers = OrderedDict(
                    thermo=dict(files=[], summaries={}, name="Thermodynamics"),
                    kinetics=dict(files=[], summaries={}, name="Kinetics"),
                    transport=dict(files=[], summaries={}, name="Transport"),
                    reactors=dict(files=[], summaries={}, name="Reactor Networks"),
                    onedim=dict(files=[], summaries={}, name="One-Dimensional Flames"),
                    multiphase=dict(files=[], summaries={}, name="Multiphase Mixtures"),
                    surface_chemistry=dict(
                        files=[], summaries={}, name="Surface Chemistry"
                    ),
                )

                python_examples = list(Path(input_folder).resolve().glob("*/*.py"))
                uptodate2 = uptodate.copy()
                uptodate2["d"] = headers.keys()
                uptodate2["f"] = list(map(str, python_examples))

                for py_ex_file in python_examples:
                    ex_category = py_ex_file.parent.stem
                    headers[ex_category]["files"].append(py_ex_file)
                    mod = ast.parse(py_ex_file.read_bytes())
                    for node in mod.body:
                        if isinstance(node, ast.Expr) and isinstance(
                            node.value, ast.Str
                        ):
                            doc = node.value.s.strip().split("\n\n")[0].strip()
                            if not doc.endswith("."):
                                doc += "."
                            break
                    headers[ex_category]["summaries"][py_ex_file.name] = doc

                    out_name = self.kw["output_folder"].joinpath(
                        example_folder,
                        ex_category,
                        py_ex_file.with_suffix(".py.html").name,
                    )
                    yield {
                        "basename": self.name,
                        "name": str(out_name),
                        "file_dep": examples_template_deps + [py_ex_file],
                        "targets": [out_name],
                        "actions": [
                            (
                                render_example,
                                [py_ex_file, out_name, input_folder, example_folder],
                            )
                        ],
                        # This is necessary to reflect changes in blog title,
                        # sidebar links, etc.
                        "uptodate": [
                            utils.config_changed(uptodate2, "build_examples:source")
                        ],
                        "clean": True,
                    }

                    out_name = self.kw["output_folder"].joinpath(
                        example_folder, ex_category, py_ex_file.name
                    )
                    yield {
                        "basename": self.name,
                        "name": out_name,
                        "file_dep": [py_ex_file],
                        "targets": [out_name],
                        "actions": [(utils.copy_file, [py_ex_file, out_name])],
                        "clean": True,
                    }

                for head in headers.keys():
                    headers[head]["files"] = natsort.natsorted(
                        headers[head]["files"], alg=natsort.IC
                    )

                out_name = self.kw["output_folder"].joinpath(
                    example_folder, self.kw["index_file"]
                )
                yield {
                    "basename": self.name,
                    "name": str(out_name),
                    "file_dep": index_template_deps,
                    "targets": [out_name],
                    "actions": [
                        (
                            render_example_index,
                            ["python", headers, input_folder, example_folder, out_name],
                        )
                    ],
                    # This is necessary to reflect changes in blog title,
                    # sidebar links, etc.
                    "uptodate": [
                        utils.config_changed(uptodate2, "build_examples:folder")
                    ],
                    "clean": True,
                }

            #########################################################
            # Build the Matlab examples
            #########################################################
            elif "matlab" in example_folder:
                template_deps = self.site.template_system.template_deps(
                    "matlab-example-index.tmpl"
                )
                p = Path(input_folder)
                headers = {"examples": {"name": "Examples"}}
                files = []
                summaries = {}
                for ex_file in p.iterdir():
                    if (
                        "tut" in ex_file.name
                        or ex_file.name == "README"
                        or "test" in ex_file.name
                    ):
                        continue
                    if (
                        ex_file.suffix in self.ignored_extensions
                        or ex_file.name == ".DS_Store"
                    ):
                        continue
                    files.append(ex_file)
                    doc = ""
                    with open(ex_file) as mfile:
                        for line in mfile:
                            line = line.strip()
                            if line.startswith("%"):
                                doc = line.strip("%").strip()
                            if doc:
                                break
                    name = ex_file.stem.replace("_", " ")
                    if doc.lower().replace("_", " ").startswith(name):
                        doc = doc[len(name):].strip()
                    summaries[ex_file.name] = doc
                headers["examples"]["summaries"] = summaries
                this_files = list(map(str, files))
                headers["examples"]["files"] = natsort.natsorted(
                    this_files, alg=natsort.IC
                )

                uptodate2 = uptodate.copy()
                uptodate2["d"] = headers.keys()
                uptodate2["f"] = list(map(str, files))

                rel_output_name = os.path.join(example_folder, self.kw["index_file"])

                # Render Matlab examples index file
                out_name = os.path.join(self.kw["output_folder"], rel_output_name)
                yield {
                    "basename": self.name,
                    "name": out_name,
                    "file_dep": template_deps,
                    "targets": [out_name],
                    "actions": [
                        (
                            render_example_index,
                            ["matlab", headers, input_folder, example_folder, out_name],
                        )
                    ],
                    # This is necessary to reflect changes in blog title,
                    # sidebar links, etc.
                    "uptodate": [
                        utils.config_changed(uptodate2, "build_examples:folder")
                    ],
                    "clean": True,
                }

                for f in files:
                    if str(f) == ".DS_Store":
                        continue
                    if f.suffix in self.ignored_extensions:
                        continue
                    in_name = str(f.resolve())
                    # Record file names
                    f_name = str(f.name)
                    rel_output_name = os.path.join(example_folder, f_name + ".html")
                    # Set up output name
                    out_name = os.path.join(self.kw["output_folder"], rel_output_name)
                    # Yield task
                    yield {
                        "basename": self.name,
                        "name": out_name,
                        "file_dep": template_deps + [in_name],
                        "targets": [out_name],
                        "actions": [
                            (
                                render_listing,
                                [in_name, out_name, input_folder, example_folder],
                            )
                        ],
                        # This is necessary to reflect changes in blog title,
                        # sidebar links, etc.
                        "uptodate": [
                            utils.config_changed(uptodate, "build_examples:source")
                        ],
                        "clean": True,
                    }

                    rel_output_name = os.path.join(example_folder, f_name)
                    out_name = os.path.join(self.kw["output_folder"], rel_output_name)
                    yield {
                        "basename": self.name,
                        "name": out_name,
                        "file_dep": [in_name],
                        "targets": [out_name],
                        "actions": [(utils.copy_file, [in_name, out_name])],
                        "clean": True,
                    }

            #########################################################
            # Build the Jupyter examples
            #########################################################
            elif "jupyter" in example_folder:
                template_deps = self.site.template_system.template_deps(
                    "jupyter-example-index.tmpl"
                )  # NOQA: E501
                headers = OrderedDict(
                    thermo={"name": "Thermodynamics"},
                    reactors={"name": "Reactor Networks"},
                    flames={"name": "One-Dimensional Flames"},
                )

                def get_b64_str(p, dir, img_fname):
                    img_path = p / dir / img_fname
                    with open(img_path, "rb") as img_file:
                        b64_str = base64.b64encode(img_file.read()).decode("utf-8")
                    mime = mimetypes.guess_type(img_path.name)[0]
                    b64_str = "data:{mime};base64,{b64_str}".format(
                        mime=mime, b64_str=b64_str
                    )
                    return b64_str

                p = Path(input_folder)
                cache_folder = Path(self.kw["cache_folder"])
                files = []
                for ex_category in p.iterdir():
                    if not ex_category.is_dir() or ex_category.name.startswith("."):
                        continue
                    if ex_category.suffix in self.ignored_extensions:
                        continue
                    summaries = {}
                    this_header_files = []
                    for f in ex_category.iterdir():
                        if f.suffix in self.ignored_extensions or f.is_dir():
                            continue
                        if f.name == ".ipynb_checkpoints" or f.name == ".DS_Store":
                            continue
                        this_header_files.append(str(f))
                        with open(f, "r") as pyfile:
                            data = json.load(pyfile)
                        doc = ""
                        for cell in data["cells"]:
                            if cell["cell_type"] != "markdown":
                                continue
                            if not doc:
                                doc = cell["source"][0].replace("#", "").strip()
                            for i, s in enumerate(cell["source"]):
                                if "img" in s:
                                    img = lxml.html.fromstring(s)
                                    img_fname = img.attrib["src"]
                                    b64_str = get_b64_str(p, ex_category, img_fname)
                                    img.attrib["src"] = b64_str
                                    new_img = lxml.html.tostring(img).decode("utf-8")
                                    cell["source"][i] = re.sub("<img.*/>", new_img, s)
                                elif "![" in s:
                                    img_alt, img_fname = re.findall(
                                        r"!\[(.*?)\]\((.*?)\)", s
                                    )[0]
                                    if "attachment" in img_fname:
                                        img_fname = img_fname.split(":", 1)[1]
                                        mime = mimetypes.guess_type(img_fname)[0]
                                        b64_src = cell["attachments"][img_fname][mime]
                                        b64_str = "data:{mime};base64,{b64_src}".format(
                                            mime=mime, b64_src=b64_src
                                        )
                                    else:
                                        b64_str = get_b64_str(p, ex_category, img_fname)

                                    new_img = '<img src="{b64_str}" alt="{img_alt}"/>'.format(  # NOQA: E501
                                        b64_str=b64_str, img_alt=img_alt
                                    )
                                    cell["source"][i] = re.sub(
                                        r"!\[.*?\]\(.*?\)", new_img, s
                                    )
                                else:
                                    continue

                        cache_file = (
                            cache_folder / example_folder / f.parent.stem / f.name
                        )
                        cache_file.parent.mkdir(parents=True, exist_ok=True)
                        files.append(cache_file)
                        with open(cache_file, "w") as jfile:
                            json.dump(data, jfile)
                        summaries[str(f).split("/")[-1]] = doc
                    headers[ex_category.stem]["summaries"] = summaries
                    this_header_files = natsort.natsorted(
                        this_header_files, alg=natsort.IC
                    )
                    headers[ex_category.stem]["files"] = this_header_files

                uptodate2 = uptodate.copy()
                uptodate2["d"] = headers.keys()
                uptodate2["f"] = list(map(str, files))

                rel_output_name = os.path.join(example_folder, self.kw["index_file"])

                # Render Jupyter examples index file
                out_name = os.path.join(self.kw["output_folder"], rel_output_name)
                yield {
                    "basename": self.name,
                    "name": out_name,
                    "file_dep": template_deps,
                    "targets": [out_name],
                    "actions": [
                        (
                            render_example_index,
                            [
                                "jupyter",
                                headers,
                                input_folder,
                                example_folder,
                                out_name,
                            ],
                        )
                    ],
                    # This is necessary to reflect changes in blog title,
                    # sidebar links, etc.
                    "uptodate": [
                        utils.config_changed(uptodate2, "build_examples:folder")
                    ],
                    "clean": True,
                }

                for f in files:
                    if str(f) == ".DS_Store":
                        continue
                    if f.suffix in self.ignored_extensions:
                        continue
                    in_name = str(f.resolve())
                    # Record file names
                    parent = str(f.parent.stem)
                    f_name = str(f.name)
                    rel_output_name = os.path.join(
                        example_folder, parent, f_name + ".html"
                    )
                    # Set up output name
                    out_name = os.path.join(self.kw["output_folder"], rel_output_name)
                    # Yield task
                    yield {
                        "basename": self.name,
                        "name": out_name,
                        "file_dep": template_deps + [in_name],
                        "targets": [out_name],
                        "actions": [
                            (
                                render_listing,
                                [in_name, out_name, input_folder, example_folder],
                            )
                        ],
                        # This is necessary to reflect changes in blog title,
                        # sidebar links, etc.
                        "uptodate": [
                            utils.config_changed(uptodate, "build_examples:source")
                        ],
                        "clean": True,
                    }

                    src_in_name = str((p / parent / f_name).resolve())
                    rel_output_name = os.path.join(example_folder, parent, f_name)
                    out_name = os.path.join(self.kw["output_folder"], rel_output_name)
                    yield {
                        "basename": self.name,
                        "name": out_name,
                        "file_dep": [src_in_name, in_name],
                        "targets": [out_name],
                        "actions": [(utils.copy_file, [src_in_name, out_name])],
                        "clean": True,
                    }
