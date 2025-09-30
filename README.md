# Re-SHACL (TODO)

## Install Libraries:
Use `pip` to install the libraries listed in the `requirements.txt` file.
* `pip install -r requirements.txt`

## DataSet (TODO):
Please download datasets:
  * EnDe-Lite50(without_Ontology).ttl : [Download](https://drive.google.com/file/d/14RXnJZL9e6ZdbFtfPH69t1lI5Idbnkze/view?usp=drive_link)
  * EnDe-Lite100(without_Ontology).ttl : [Download](https://drive.google.com/file/d/1xWMp2mSEk0i7X_nHp3JjZRnvt3bPiz96/view?usp=drive_link)
  * EnDe-Lite1000(without_Ontology).ttl : [Download](https://drive.google.com/file/d/1B2Xbukuj93vHeRBvbgnPCoYAxbHcC7Hg/view?usp=drive_link)
    * The genaration of EnDe-Lite datasets cover 30 classes.

  * LUBM datasets from: https://data.uni-hannover.de/dataset/trav-shacl-benchmarks-experimental-settings-and-evaluation/resource/3dcefa6d-d57e-4de7-bc11-56227ae4e119?inner_span=True
    * We follow the LUBM datasets for the paper **Trav-SHACL: Efficiently validating networks of SHACL constraints**.
    * Our experiments used only three SKGs and three MKGs.

Please add all datasets to `source/Datasets/…`.

## Shapes Graph:
The shapes graphs needed for the experiment have been stored in the folder `source/ShapesGraphs`.
  * Shape_30 is dedicated to the validation of the EnDe datasets.
  * For different sizes of LUBM datasets we use the three schemas provided in the paper **Trav-SHACL: Efficiently validating networks of SHACL constraints**.

## Experiments:
Run the experiments by running the script `run.py`.

```
Choose your method: 
1. Built-in
2. Custom
```
Where
* `1. Built-in` is an option to use the same data graphs from our paper.
* `2. Custom` is the option to use the user's own data graphs.
  
Enter the option number and enter, then confirm the option. If you want to use the same experimental data as ours, please select `1`.

Then you can choose data graph and shape graph:
```
Choose your dataset: 
1. EnDe
2. LUBM
```
Please add the desired Datasets to the specified folder before running the experiment.
 * If you selected the `1.EnDe` dataset, then the corresponding shapes graph is `Shape_30`.
 * If you selected the `2.LUBM` dataset, then there are three shapes graphs available:
    * ```
      Choose your shapes graph: 
      1. source/ShapesGraphs/lubm/schema1.ttl
      2. source/ShapesGraphs/lubm/schema3.ttl
      3. source/ShapesGraphs/lubm/schema2.ttl
      ```
Finally, you can choose the method you want to use:
```
Choose your method: 
1. pyshacl
2. pyshacl-rdfs
3. pyshacl-owl
4. reshacl
5. class-reshacl 
```
The experiment will be performed after you have determined the desired method.
  
The validation results are recorded in the folder corresponding to each data set in `Outputs`.
  * The validation result **graph** is stored in the `validationGraph` folder. 
  * Validation reports are stored in the `validationReports` folder. 
  * The runtime and number of violations are recorded in the file `RunTimeResults.txt`.



