import os
import argparse
import scipy.ndimage
import numpy as np
from PIL import Image
import time
from Constants import SIZE, RADIUS
from tensorflow.keras.models import model_from_json

"""
Tested using Python 3.8.11, numpy 1.20.3, scikit-image 0.18.1, pillow 8.3.1, keras 2.4.3, tensorflow 2.3.1
Tested using 20x EFI Images from Olympus Slide Scanner as outlined in paper

This program uses a two-step method to detect synaptic boutons on histological images
The first step, a "proposal" algorithm, quickly scans through the image and detects potential boutons by looking for 
groups of pixels that are darker than the surrounding tissue

The second step, a "verification" algorithm, scans each of these potential boutons and verifies them via a pretrained
convolutional neural network.

Command line arguments allow the user to select how the program will search for input images
--folder: Intended for a single histological section.  Required format of this folder is as follows
    Folder
        Image001.tif  <-- RGB or Greyscale microscopy image.  Should be 345 nm/pixel
        Image002.tif  <-- Image001, Image002,... will be stitched together horizontally
        Mask.jpg <-- Optional Mask with same resolution as Image.jpg.  Boutons will only be found in white areas
--directory: Intended for an entire brain with many histological sections.  Required format
    Directory
        Section_01
            Image.jpg
            Mask.jpg
        Section_02
            Image.jpg
            Mask.jpg
        ...
--order: Intended for multiple brains, each with many histological sections.  If the brains are organized as so
    Order.txt
    Brain_01
        Section_01
            Image.jpg
            Mask.jpg
        ...
    Brain_02
        Section_01
            Image.jpg
            Mask.jpg
        ...
    ...
    Then Order.txt should read as so:
    Brain_01
    Brain_02
    ...        

Command line arguments allow the user to select three different types of output.  These output files will be placed
in the folder where the Image.jpg file was located
--png: Program will output a png file with the same dimensions as the image file, with red 5x5 boutons placed on it
--svg: Program will output a svg file with bouton symbols placed on it
--csv: Program will output a csv file with the x,y positions of each bouton
"""


class OutputFileType:
    """
    Class to hold user selected output formats
    """

    def __init__(self):
        self.svg = False
        self.csv = False
        self.png = False

    def any(self):
        return self.svg or self.csv or self.png


def load_json_model(name):
    """
    Loads Boutons.json and Boutons.h5 into a keras model
    """
    json_file = open(name + '.json', 'r')
    loaded_model_json = json_file.read()
    json_file.close()
    loaded_model = model_from_json(loaded_model_json)
    # load weights into new model
    loaded_model.load_weights(name + ".h5")
    print("Loaded model from disk")
    return loaded_model


def file_to_array(filepath, average=True):
    im = Image.open(filepath)
    arr = np.array(im)
    if average:
        if len(arr.shape) > 2:
            arr = np.sum(arr, axis=2)
            arr = arr * (255 / arr.max())
    return arr.astype(np.int16)


def detect_local_minima(arr, mask=None):
    scale = 4
    dilated = scipy.ndimage.grey_dilation(arr, size=(scale, scale))
    eroded = scipy.ndimage.grey_erosion(arr, size=(scale, scale))
    x1 = arr == eroded
    x2 = arr < dilated - .01
    x = np.logical_and(x1, x2)
    if mask is not None:
        x = x * mask
    x[:RADIUS, :] = False
    x[-RADIUS:, :] = False
    x[:, :RADIUS] = False
    x[:, -RADIUS:] = False
    return np.where(x)


def local_minima_generate_points(arr, mask=None):
    float_arr = arr.astype(np.float32)
    scale = 1
    fuzzy = scipy.ndimage.gaussian_filter(float_arr, sigma=scale)
    minima = np.array(detect_local_minima(fuzzy, mask)).T
    assert minima.shape[0] > 0
    X = np.zeros(shape=(minima.shape[0], SIZE, SIZE, 1), dtype=np.float32)
    for i in range(minima.shape[0]):
        x, y = minima[i, :]
        X[i, :, :, 0] = arr[x - RADIUS:x + RADIUS, y - RADIUS:y + RADIUS]
    return X, minima


class CreatePNG():
    def __init__(self, output_bouton_png_name, artboard_size_xy, downscale=1.0):
        self.output_name = output_bouton_png_name
        self.x = artboard_size_xy[0]
        self.y = artboard_size_xy[1]
        self.arr = np.zeros(shape=(self.x, self.y), dtype=bool)
        self.radius = 3
        self.symbol_shape = np.zeros((self.radius * 2 + 1, self.radius * 2 + 1), dtype=bool)
        self.downscale = downscale
        for x in range(self.radius * 2 + 1):
            for y in range(self.radius * 2 + 1):
                if (x - self.radius) ** 2 + (y - self.radius) ** 2 <= self.radius ** 2:
                    self.symbol_shape[x, y] = True

    def add_symbol(self, location_xy):
        try:
            y, x = location_xy[0], location_xy[1]
            self.arr[x - self.radius: x + self.radius + 1, y - self.radius: y + self.radius + 1] = \
                np.logical_or(self.arr[x - self.radius: x + self.radius + 1, y - self.radius: y + self.radius + 1],
                              self.symbol_shape)
        except ValueError as e:
            pass

    def output(self):
        image_array = np.zeros((self.arr.shape[0], self.arr.shape[1], 4), dtype=np.uint8)
        image_array[:, :, 0] = color[0]  # Convert to image array
        image_array[:, :, 1] = color[1]
        image_array[:, :, 2] = color[2]
        image_array[:, :, 3] = self.arr * 255
        image = Image.fromarray(image_array, 'RGBA')
        image = image.resize((int(self.arr.shape[1] * self.downscale), int(self.arr.shape[0] * self.downscale)),
                             Image.NEAREST)
        image.save(self.output_name)


class CreateSVG():
    def __init__(self, output_svgname, artboard_size_xy, input_image):
        self.output_svgname = output_svgname
        x = artboard_size_xy[1]
        y = artboard_size_xy[0]
        self.strings = [
            """<svg id="_Boutons" data-name="Boutons" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %s %s"><defs><style>.cls-1{fill:black;stroke:black;}</style></defs>""" % (
            x, y)]

    def add_symbol(self, location_xy):
        self.strings.append("""<circle class="cls-1" cx="%s" cy="%s" r="1"/>""" % (location_xy[0], location_xy[1]))

    def output(self):
        self.strings.append("""</svg>""")
        string = "".join(self.strings)
        with open(self.output_svgname, 'w+') as f:
            f.write(string)


def generate_border_and_mask(mask_file_path):
    if mask_file_path is None:
        return None, None
    else:
        mask = file_to_array(mask_file_path)
        mask = mask > 128
        border = np.bitwise_and(mask, np.invert(scipy.ndimage.morphology.binary_erosion(mask)))
        return mask, border


def count_section(directory, model, oft):
    input_mask = os.path.join(directory, "Mask.png")
    output_image = os.path.join(directory, "Image")
    output_svg = os.path.join(directory, "Boutons.svg")
    output_png = os.path.join(directory, "Boutons.png")
    output_csv = os.path.join(directory, "BoutonCount.csv")
    arr = None
    start_time = time.time()
    for extension in ["png", "jpg", "tif"]:
        image = "%s.%s" % (output_image, extension)
        if os.path.exists(image):
            arr = file_to_array(image).astype(np.float32)
            arr = (arr - arr.min()) / (arr.max() - arr.min())
            arr = (arr * 255).astype(np.uint8)
    else:
        """
        Loads images exported by either CellSens or VS-ASW into memory
        """
        files = [f for f in sorted(os.listdir(directory)) if
                 os.path.isfile(os.path.join(directory, f)) and (f.endswith(".tif") or f.endswith("png")) and (
                             "ome" in f or "IMAGE" in f)]
        if len(files) == 0:  # If exported through CellSens
            for r, d, f in os.walk(directory, topdown=False):
                for file in f:
                    if file.endswith(".tif"):
                        arr = file_to_array(os.path.join(r, file)).astype(np.float32)
        else:
            try:
                arrs = []
                for file in files:  # If exported through VS-ASW
                    arrs.append(file_to_array(os.path.join(directory, file)).astype(np.float32))
                if arrs[0].shape[0] > arrs[0].shape[1]:
                    arr = np.concatenate(arrs, axis=1)
                else:
                    arr = np.concatenate(arrs, axis=0)
            except OSError:
                print("Images for %s do not exist" % directory)
                return
        """
        Saves imported image as "Image.jpg"
        This makes it easier to access in the future
        """
        print("Working on %s" % directory)
        assert arr is not None
        arr = (arr - arr.min()) / (arr.max() - arr.min())
        arr = (arr * 255).astype(np.uint8)
        im = Image.fromarray(arr).convert("L")
        im.save(output_image + ".png")
        """
        Deletes imported images
        """
        """
        if len(files) == 0:
            for r, d, f in os.walk(directory, topdown=False):  # If exported through CellSens
                for file in f:
                    if file.endswith(".tif") or "SUCCESS" in file or ".db" in file:
                        os.remove(os.path.join(r, file))
                if r != directory:
                    os.rmdir(r)
        else:
            for file in files:
                os.remove(os.path.join(directory, file))
        """
    if oft.any():
        if os.path.exists(output_csv):
            true_cells = np.loadtxt(output_csv, delimiter=",").astype(np.int16)
        else:
            mask = None
            if os.path.exists(input_mask):
                mask, _ = generate_border_and_mask(input_mask)
                if arr.shape != mask.shape:
                    print("*******")
                    print("Mask and image have different shape for " % directory)
                    print("*******")
                    return
            print("Load Time: %.2f" % (time.time() - start_time))
            start_time = time.time()
            X, COMs = local_minima_generate_points(arr, mask)
            print("Proposal Time: %.2f" % (time.time() - start_time))
            start_time = time.time()
            print("%s boutons proposed" % X.shape[0])
            output = model.predict(X)
            print("ML Time: %.2f" % (time.time() - start_time))
            start_time = time.time()
            true_cells = COMs[output[:, 0] > 0.80, :]
        print("%s boutons detected" % true_cells.shape[0])
        if oft.svg and not os.path.exists(output_svg):
            svg = CreateSVG(output_svg, arr.shape, output_image)
            for i in range(true_cells.shape[0]):
                svg.add_symbol(location_xy=(true_cells[i, 1], true_cells[i, 0]))
            svg.output()
        if oft.png:
            png = CreatePNG(output_png, arr.shape, downscale=1)
            for i in range(true_cells.shape[0]):
                png.add_symbol(location_xy=(true_cells[i, 1], true_cells[i, 0]))
            png.output()
        if oft.csv:
            np.savetxt(output_csv, true_cells, delimiter=",")
        print("Output Time: %.2f" % (time.time() - start_time))


def count_brain(brain_directory, model, oft):
    for directory in sorted(os.listdir(brain_directory)):
        directory = os.path.join(brain_directory, directory)
        if os.path.isdir(directory):
            count_section(directory, model, oft)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="This program uses the pretrained NN to search for boutons")
    parser.add_argument("-b", "--brain", type=str, help="Brain level directory")
    parser.add_argument("-o", "--order", type=str, help="Order metadata file for multiple brains")
    parser.add_argument("-e", "--section", type=str, help="File for one section from one brain")
    parser.add_argument("-p", "--png", action="store_true", help="Output png file Boutons.png")
    parser.add_argument("-s", "--svg", action="store_true", help="Output svg file Boutons.svg")
    parser.add_argument("-c", "--csv", action="store_true", help="Output csv file Boutons.csv")
    parser.add_argument("-l", "--color", type=str, help="Color of boutons, in format RR,GG,BB Ex: '255,0,0'")
    args = parser.parse_args()
    Image.MAX_IMAGE_PIXELS = None
    model = load_json_model("Boutons")
    oft = OutputFileType()
    oft.svg, oft.png, oft.csv = args.svg, args.png, args.csv
    global color
    color = [255, 0, 0]
    if args.color is not None:
        color = [int(''.join(c for c in s if c.isdigit())) for s in args.color.split(",")]
        assert len(color) == 3 or len(color) == 4
    if args.brain is not None:
        count_brain(args.brain, model, oft)
    elif args.order is not None:
        dir_name = os.path.dirname(args.order)
        with open(args.order) as f:
            for line in f:
                line = os.path.join(dir_name, line.strip())
                count_brain(line, model, oft)
    elif args.section is not None:
        count_section(args.section, model, oft)
