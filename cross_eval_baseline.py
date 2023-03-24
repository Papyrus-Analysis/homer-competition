"""
This code base on the official Pytorch TORCHVISION OBJECT DETECTION FINETUNING TUTORIAL
https://pytorch.org/tutorials/intermediate/torchvision_tutorial.html

A Resnet18 was trained during 60 epochs.
The mean average precision at 0.5 IOU was 0.16
"""
import glob
import json
import os

import torch
import wandb
from PIL import Image
from PIL import ImageFile
from torchvision import transforms
from options.cross_val_options import CrossValOptions
from utils import misc

ImageFile.LOAD_TRUNCATED_IMAGES = True
import torchvision
from torchvision.models.detection.faster_rcnn import FastRCNNPredictor
import frcnn.transforms as T
from frcnn.engine import train_one_epoch, evaluate
import frcnn.utils as utils

from pycocotools.coco import COCO
from pycocotools.cocoeval import COCOeval

mapping = {
    7: 1,
    8: 2,
    9: 3,
    14: 4,
    17: 5,
    23: 6,
    33: 7,
    45: 8,
    59: 9,
    77: 10,
    100: 11,
    107: 12,
    111: 13,
    119: 14,
    120: 15,
    144: 16,
    150: 17,
    161: 18,
    169: 19,
    177: 20,
    186: 21,
    201: 22,
    212: 23,
    225: 24,
}

idx_to_letter = {v: k for k, v in mapping.items()}


class HomerCompDataset(torch.utils.data.Dataset):
    def __init__(self, dataset_path, transforms=None, isTrain=False, fold=1, k_fold=5):
        self.transforms = transforms
        images = glob.glob(os.path.join(dataset_path, '**', '*.jpg'), recursive=True)
        images.extend(glob.glob(os.path.join(dataset_path, '**', '*.JPG'), recursive=True))
        images.extend(glob.glob(os.path.join(dataset_path, '**', '*.png'), recursive=True))
        images = sorted([os.path.basename(x) for x in images])
        folds = list(misc.chunks(images, k_fold))
        if isTrain:
            del folds[fold]
            images = misc.flatten(folds)
        else:
            images = folds[fold]

        jFile = open(os.path.join(dataset_path, "HomerCompTrainingReadCoco.json"))
        self.data = json.load(jFile)
        self.dataset_path = dataset_path
        jFile.close()
        ids = []
        for i, image in enumerate(self.data['images']):
            if os.path.basename(image['file_name']) in images:
                ids.append(i)
        self.imgs = ids

    def __getitem__(self, idx):
        # load images and masks
        image = self.data['images'][self.imgs[idx]]
        img_url = image['img_url'].split('/')
        image_file = img_url[-1]
        image_folder = img_url[-2]
        image_id = image['bln_id']
        annotations = self.data['annotations']
        boxes = []
        labels = []
        for annotation in annotations:
            if image_id == annotation['image_id']:
                try:
                    labels.append(mapping[int(annotation['category_id'])])
                except:
                    continue
                x, y, w, h = annotation['bbox']
                xmin = x
                xmax = x + w
                ymin = y
                ymax = y + h
                boxes.append([xmin, ymin, xmax, ymax])
        # convert everything into a torch.Tensor
        boxes = torch.as_tensor(boxes, dtype=torch.float32)
        # there is only one class
        labels = torch.as_tensor(labels, dtype=torch.int64)

        image_id = torch.tensor([idx])
        area = (boxes[:, 3] - boxes[:, 1]) * (boxes[:, 2] - boxes[:, 0])
        # suppose all instances are not crowd
        num_objs = labels.shape[0]
        iscrowd = torch.zeros((num_objs,), dtype=torch.int64)

        target = {}
        target["boxes"] = boxes
        target["labels"] = labels
        target["image_id"] = image_id
        target["area"] = area
        target["iscrowd"] = iscrowd

        src_folder = os.path.join(self.dataset_path, "images", "homer2")
        fname = os.path.join(src_folder, image_folder, image_file)
        img = Image.open(fname).convert('RGB')
        img.resize((1000, round(img.size[1] * 1000.0 / float(img.size[0]))), Image.BILINEAR)
        if self.transforms is not None:
            img, target = self.transforms(img, target)
        return img, target

    def __len__(self):
        return len(self.imgs)


def get_transform(train):
    transforms = []
    transforms.append(T.PILToTensor())
    transforms.append(T.ConvertImageDtype(torch.float))
    if train:
        transforms.append(T.RandomHorizontalFlip(0.5))
        transforms.append(T.FixedSizeCrop((672, 672)))
        transforms.append(T.RandomPhotometricDistort())
    else:
        transforms.append(T.FixedSizeCrop((672, 672)))
    return T.Compose(transforms)


def val(args, fold, k_fold):
    working_dir = os.path.join(args.checkpoints_dir, args.name, f'fold_{fold}')
    device = torch.device('cpu')
    num_classes = 25

    model = torchvision.models.detection.fasterrcnn_resnet50_fpn(pretrained=True)
    in_features = model.roi_heads.box_predictor.cls_score.in_features
    model.roi_heads.box_predictor = FastRCNNPredictor(in_features, num_classes)
    model.to(device)

    checkpoint = torch.load(os.path.join(working_dir, "model_detection.pt"), map_location=device)

    model.load_state_dict(checkpoint['model_state_dict'])
    model.eval()

    trans = []
    trans.append(T.PILToTensor())
    trans.append(T.ConvertImageDtype(torch.float))
    trans = T.Compose(trans)

    dataset_test = HomerCompDataset(args.dataset, transforms=trans, isTrain=False, fold=fold, k_fold=k_fold)

    data_loader_test = torch.utils.data.DataLoader(
        dataset_test, batch_size=1, shuffle=False, num_workers=4,
        collate_fn=utils.collate_fn)

    jFile = open(os.path.join("template.json"))
    json_output = json.load(jFile)
    jFile.close()

    jFile = open(os.path.join(args.dataset, "HomerCompTrainingReadCoco.json"))
    test_gt = json.load(jFile)
    jFile.close()

    img_ids = []

    for images, targets in data_loader_test:

        image = images[0]
        idx = targets[0]['image_id'].item()

        image_id = json_output['images'][dataset_test.imgs[idx]]['bln_id']
        img_ids.append(image_id)

        # Patch wise predictions
        for i in range(0, image.shape[1], 672):
            for j in range(0, image.shape[2], 672):
                crop = transforms.functional.crop(image, i, j, 672, 672)
                crop = torch.unsqueeze(crop, 0)
                result = model(crop)
                boxes = result[0]['boxes'].int()
                scores = result[0]['scores']
                preds = result[0]['labels']
                if len(boxes) == 0:
                    continue

                for box, label, score in zip(boxes, preds, scores):
                    annotation = dict()
                    annotation['image_id'] = image_id
                    annotation['category_id'] = idx_to_letter[label.item()]
                    annotation['bbox'] = [box[0].item() + j, box[1].item() + i, (box[2] - box[0]).item(),
                                          (box[3] - box[1]).item()]
                    annotation['score'] = score.item()

                    json_output['annotations'].append(annotation)

    for annotation in list(test_gt['annotations']):
        if annotation['image_id'] not in img_ids:
            test_gt['annotations'].remove(annotation)

    with open("gt.json", "w") as outfile:
        json.dump(test_gt, outfile, indent=4)

    with open("predictions.json", "w") as outfile:
        json.dump(json_output, outfile, indent=4)

    jFile = open(os.path.join("predictions.json"))
    predictions = json.load(jFile)
    jFile.close()

    jFile = open(os.path.join("gt.json"))
    gt = json.load(jFile)
    jFile.close()

    for annotation in list(gt['annotations']):
        if annotation['tags']['BaseType'][0] == 'bt3':
            gt['annotations'].remove(annotation)

    for annotation in gt['annotations']:
        annotation['iscrowd'] = 0

    with open("gt_tmp.json", "w") as outfile:
        json.dump(gt, outfile, indent=4)

    with open("pr_tmp.json", "w") as outfile:
        json.dump(predictions['annotations'], outfile, indent=4)

    cocoGt = COCO('gt_tmp.json')
    cocoDt = cocoGt.loadRes("pr_tmp.json")

    os.remove('gt_tmp.json')
    os.remove('pr_tmp.json')

    cocoEval = COCOeval(cocoGt, cocoDt, 'bbox')
    cocoEval.evaluate()
    cocoEval.accumulate()
    cocoEval.summarize()

    val_dict = {
        f'val/mAP_0.5:0.95': cocoEval.stats[0],
        f'val/mAP_0.5': cocoEval.stats[1],
        f'val/mAP_0.75': cocoEval.stats[2],
    }

    print(val_dict)

    wandb.log(val_dict)


def train(args, fold, k_fold):
    device = torch.device('cuda') if torch.cuda.is_available() else torch.device('cpu')
    working_dir = os.path.join(args.checkpoints_dir, args.name, f'fold_{fold}')
    os.makedirs(working_dir, exist_ok=True)
    num_classes = 25
    dataset = HomerCompDataset(args.dataset, transforms=get_transform(True), isTrain=True, fold=fold, k_fold=k_fold)
    print(f'N images train: {len(dataset)}')
    val_set = HomerCompDataset(args.dataset, transforms=get_transform(False), isTrain=False, fold=fold, k_fold=k_fold)
    print(f'N images val: {len(val_set)}')

    data_loader = torch.utils.data.DataLoader(
        dataset, batch_size=args.batch_size, shuffle=True, num_workers=4,
        collate_fn=utils.collate_fn)

    data_loader_test = torch.utils.data.DataLoader(
        val_set, batch_size=args.batch_size, shuffle=False, num_workers=4,
        collate_fn=utils.collate_fn)

    model = torchvision.models.detection.fasterrcnn_resnet50_fpn(pretrained=True)
    in_features = model.roi_heads.box_predictor.cls_score.in_features
    model.roi_heads.box_predictor = FastRCNNPredictor(in_features, num_classes)
    model.to(device)

    params = [p for p in model.parameters() if p.requires_grad]
    optimizer = torch.optim.SGD(params, lr=0.001, momentum=0.8, weight_decay=0.0004)
    lr_scheduler1 = torch.optim.lr_scheduler.StepLR(optimizer, step_size=1, gamma=2)
    lr_scheduler2 = torch.optim.lr_scheduler.StepLR(optimizer, step_size=3, gamma=0.85)
    num_epochs = args.nepochs
    for epoch in range(num_epochs):
        train_one_epoch(model, optimizer, data_loader, device, epoch, print_freq=10)
        if epoch < 4:
            lr_scheduler1.step()
        elif 9 < epoch < 50:
            lr_scheduler2.step()
        # coco_evaluator = evaluate(model, data_loader_test, device=device)
        torch.save({
            'epoch': epoch,
            'model_state_dict': model.state_dict(),
            'optimizer_state_dict': optimizer.state_dict(),
        }, os.path.join(working_dir, "model_detection.pt"))


if __name__ == '__main__':
    args = CrossValOptions().parse()
    for fold in range(args.k_fold):
        run = wandb.init(group=args.group,
                         name=f'{args.name}_fold-{fold}',
                         project=args.wb_project,
                         entity=args.wb_entity,
                         resume=args.resume,
                         config=args,
                         settings=wandb.Settings(_disable_stats=True),
                         mode=args.wb_mode)

        train(args, fold, args.k_fold)
        val(args, fold, args.k_fold)

        run.finish()
