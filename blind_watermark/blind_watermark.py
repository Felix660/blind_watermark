import numpy as np
import cv2
from pywt import dwt2, idwt2
import os
from scipy.stats import pearsonr


class WaterMark:
    def __init__(self, random_seed_wm, random_seed_dct, mod, mod2=None, wm_shape=None, block_shape=(4, 4)):
        # random_seed_wm, random_seed_dct 是随机种子
        # mod, mod2 用于嵌入算法的除数,理论上第一个除数要大于第二个,除数越大鲁棒性越强,但输出图片的失真越大
        self.block_shape = block_shape  # 2^n
        self.random_seed_wm = random_seed_wm
        self.random_seed_dct = random_seed_dct
        self.mod = mod
        self.mod2 = mod2
        self.wm_shape = wm_shape  # 水印的大小
        self.dwt_deep = 1

    def init_block_add_index(self, img_shape):
        # 假设原图长宽均为2的整数倍,同时假设水印为64*64,则32*32*4
        # 分块并DCT
        shape0_int, shape1_int = int(img_shape[0] / self.block_shape[0]), int(img_shape[1] / self.block_shape[1])
        if not shape0_int * shape1_int >= self.wm_shape[0] * self.wm_shape[1]:
            print("水印的大小超过图片的容量")
        self.part_shape = (shape0_int * self.block_shape[0], shape1_int * (self.block_shape[1]))
        self.block_add_index0, self.block_add_index1 = np.meshgrid(np.arange(shape0_int), np.arange(shape1_int))
        self.block_add_index0, self.block_add_index1 = self.block_add_index0.flatten(), self.block_add_index1.flatten()
        self.length = self.block_add_index0.size
        # 验证没有意义,但是我不验证不舒服斯基
        assert self.block_add_index0.size == self.block_add_index1.size

    def normalize_pic(self, img_array):
        # 如果不是偶数，那么补0
        img_shape = img_array.shape
        if not img_shape[0] % 2 == 0:
            img_array = np.concatenate((img_array, np.zeros((1, img_shape[1], 3))),
                                       axis=0)
        if not img_shape[1] % 2 == 0:
            img_array = np.concatenate((img_array, np.zeros((img_shape[0], 1, 3))),
                                       axis=1)
        return img_array

    def read_ori_img(self, filename):
        self.ori_img = cv2.imread(filename).astype(np.float32)

        self.ori_img_shape = self.ori_img.shape[:2]
        self.ori_img_YUV = cv2.cvtColor(self.ori_img, cv2.COLOR_BGR2YUV)

        # 如果不是偶数，那么补0
        self.ori_img_YUV = self.normalize_pic(self.ori_img_YUV)

        self.ha_Y, self.coeffs_Y = dwt2(self.ori_img_YUV[:, :, 0], 'haar')
        self.ha_U, self.coeffs_U = dwt2(self.ori_img_YUV[:, :, 1], 'haar')
        self.ha_V, self.coeffs_V = dwt2(self.ori_img_YUV[:, :, 2], 'haar')

        self.ha_block_shape = (
            int(self.ha_Y.shape[0] / self.block_shape[0]), int(self.ha_Y.shape[1] / self.block_shape[1]),
            self.block_shape[0], self.block_shape[1])
        strides = self.ha_Y.itemsize * (
            np.array([self.ha_Y.shape[1] * self.block_shape[0], self.block_shape[1], self.ha_Y.shape[1], 1]))
        self.ha_Y_block = np.lib.stride_tricks.as_strided(self.ha_Y.copy(), self.ha_block_shape, strides)
        self.ha_U_block = np.lib.stride_tricks.as_strided(self.ha_U.copy(), self.ha_block_shape, strides)
        self.ha_V_block = np.lib.stride_tricks.as_strided(self.ha_V.copy(), self.ha_block_shape, strides)

    def read_wm(self, filename):
        self.wm = cv2.imread(filename)[:, :, 0]
        self.wm_shape = self.wm.shape[:2]

        # 初始化块索引数组,因为需要验证块是否足够存储水印信息,所以才放在这儿
        self.init_block_add_index(self.ha_Y.shape)

        self.wm_flatten = self.wm.flatten()

        # 水印加密
        self.random_wm = np.random.RandomState(self.random_seed_wm)
        self.random_wm.shuffle(self.wm_flatten)

    def block_add_wm(self, block, index, i):

        i = i % (self.wm_shape[0] * self.wm_shape[1])

        wm_1 = self.wm_flatten[i]
        block_dct = cv2.dct(block)
        block_dct_flatten = block_dct.flatten().copy()

        block_dct_flatten = block_dct_flatten[index]
        block_dct_shuffled = block_dct_flatten.reshape(self.block_shape)
        U, s, V = np.linalg.svd(block_dct_shuffled)
        max_s = s[0]
        s[0] = (max_s - max_s % self.mod + 3 / 4 * self.mod) if wm_1 >= 128 else (
                max_s - max_s % self.mod + 1 / 4 * self.mod)
        if self.mod2:
            max_s = s[1]
            s[1] = (max_s - max_s % self.mod2 + 3 / 4 * self.mod2) if wm_1 >= 128 else (
                    max_s - max_s % self.mod2 + 1 / 4 * self.mod2)
        # s[1] = (max_s-max_s%self.mod2+3/4*self.mod2) if wm_1<128 else (max_s-max_s%self.mod2+1/4*self.mod2)

        block_dct_shuffled = np.dot(U, np.dot(np.diag(s), V))

        block_dct_flatten = block_dct_shuffled.flatten()

        block_dct_flatten[index] = block_dct_flatten.copy()
        block_dct = block_dct_flatten.reshape(self.block_shape)

        return cv2.idct(block_dct)

    def embed(self, filename):

        embed_ha_Y_block = self.ha_Y_block.copy()
        embed_ha_U_block = self.ha_U_block.copy()
        embed_ha_V_block = self.ha_V_block.copy()

        self.random_dct = np.random.RandomState(self.random_seed_dct)
        index = np.arange(self.block_shape[0] * self.block_shape[1])

        for i in range(self.length):
            self.random_dct.shuffle(index)
            embed_ha_Y_block[self.block_add_index0[i], self.block_add_index1[i]] = self.block_add_wm(
                embed_ha_Y_block[self.block_add_index0[i], self.block_add_index1[i]], index, i)
            embed_ha_U_block[self.block_add_index0[i], self.block_add_index1[i]] = self.block_add_wm(
                embed_ha_U_block[self.block_add_index0[i], self.block_add_index1[i]], index, i)
            embed_ha_V_block[self.block_add_index0[i], self.block_add_index1[i]] = self.block_add_wm(
                embed_ha_V_block[self.block_add_index0[i], self.block_add_index1[i]], index, i)

        embed_ha_Y_part = np.concatenate(embed_ha_Y_block, 1)
        embed_ha_Y_part = np.concatenate(embed_ha_Y_part, 1)
        embed_ha_U_part = np.concatenate(embed_ha_U_block, 1)
        embed_ha_U_part = np.concatenate(embed_ha_U_part, 1)
        embed_ha_V_part = np.concatenate(embed_ha_V_block, 1)
        embed_ha_V_part = np.concatenate(embed_ha_V_part, 1)

        embed_ha_Y = self.ha_Y.copy()
        embed_ha_Y[:self.part_shape[0], :self.part_shape[1]] = embed_ha_Y_part
        embed_ha_U = self.ha_U.copy()
        embed_ha_U[:self.part_shape[0], :self.part_shape[1]] = embed_ha_U_part
        embed_ha_V = self.ha_V.copy()
        embed_ha_V[:self.part_shape[0], :self.part_shape[1]] = embed_ha_V_part

        # 逆变换回去
        (cH, cV, cD) = self.coeffs_Y
        embed_ha_Y = idwt2((embed_ha_Y.copy(), (cH, cV, cD)), "haar")  # 其idwt得到父级的ha
        (cH, cV, cD) = self.coeffs_U
        embed_ha_U = idwt2((embed_ha_U.copy(), (cH, cV, cD)), "haar")  # 其idwt得到父级的ha
        (cH, cV, cD) = self.coeffs_V
        embed_ha_V = idwt2((embed_ha_V.copy(), (cH, cV, cD)), "haar")  # 其idwt得到父级的ha

        # 合并3通道
        embed_img_YUV = np.zeros(self.ori_img_YUV.shape, dtype=np.float32)
        embed_img_YUV[:, :, 0] = embed_ha_Y
        embed_img_YUV[:, :, 1] = embed_ha_U
        embed_img_YUV[:, :, 2] = embed_ha_V

        # 如果之前因为不是2的整数
        embed_img_YUV = embed_img_YUV[:self.ori_img_shape[0], :self.ori_img_shape[1]]
        embed_img = cv2.cvtColor(embed_img_YUV, cv2.COLOR_YUV2BGR)

        embed_img[embed_img > 255] = 255
        embed_img[embed_img < 0] = 0

        cv2.imwrite(filename, embed_img)

        print('隐水印嵌入成功，保存到文件 ', filename)
        for i in range(3):
            diff, _ = pearsonr(self.ori_img[:, :, i].flatten(), embed_img[:, :, i].flatten())
            print('通道{}的相似度是{}'.format(i, diff))
        print('(相似度越接近1越好)')

    def block_get_wm(self, block, index):
        block_dct = cv2.dct(block)
        block_dct_flatten = block_dct.flatten().copy()
        block_dct_flatten = block_dct_flatten[index]
        block_dct_shuffled = block_dct_flatten.reshape(self.block_shape)

        U, s, V = np.linalg.svd(block_dct_shuffled)
        max_s = s[0]
        wm_1 = 255 if max_s % self.mod > self.mod / 2 else 0
        if self.mod2:
            max_s = s[1]
            wm_2 = 255 if max_s % self.mod2 > self.mod2 / 2 else 0
            wm = (wm_1 * 3 + wm_2 * 1) / 4
        else:
            wm = wm_1
        return wm

    def extract(self, filename, out_wm_name):
        if not self.wm_shape:
            print("水印的形状未设定")
            return 0

        # 读取图片
        embed_img = cv2.imread(filename).astype(np.float32)
        embed_img_YUV = cv2.cvtColor(embed_img, cv2.COLOR_BGR2YUV)

        if not embed_img_YUV.shape[0] % 2 == 0:
            embed_img_YUV = np.concatenate((embed_img_YUV, np.zeros((1, embed_img_YUV.shape[1], 3))), axis=0)
        if not embed_img_YUV.shape[1] % 2 == 0:
            embed_img_YUV = np.concatenate((embed_img_YUV, np.zeros((embed_img_YUV.shape[0], 1, 3))), axis=1)

        embed_img_Y = embed_img_YUV[:, :, 0]
        embed_img_U = embed_img_YUV[:, :, 1]
        embed_img_V = embed_img_YUV[:, :, 2]
        coeffs_Y = dwt2(embed_img_Y, 'haar')
        coeffs_U = dwt2(embed_img_U, 'haar')
        coeffs_V = dwt2(embed_img_V, 'haar')
        ha_Y = coeffs_Y[0]
        ha_U = coeffs_U[0]
        ha_V = coeffs_V[0]

        # 初始化块索引数组
        try:
            if self.ha_Y.shape == ha_Y.shape:
                self.init_block_add_index(ha_Y.shape)
            else:
                print('你现在要解水印的图片与之前读取的原图的形状不同,这是不被允许的')
        except:
            self.init_block_add_index(ha_Y.shape)

        ha_block_shape = (
            int(ha_Y.shape[0] / self.block_shape[0]), int(ha_Y.shape[1] / self.block_shape[1]), self.block_shape[0],
            self.block_shape[1])
        strides = ha_Y.itemsize * (
            np.array([ha_Y.shape[1] * self.block_shape[0], self.block_shape[1], ha_Y.shape[1], 1]))

        ha_Y_block = np.lib.stride_tricks.as_strided(ha_Y.copy(), ha_block_shape, strides)
        ha_U_block = np.lib.stride_tricks.as_strided(ha_U.copy(), ha_block_shape, strides)
        ha_V_block = np.lib.stride_tricks.as_strided(ha_V.copy(), ha_block_shape, strides)

        extract_wm = np.array([])
        extract_wm_Y = np.array([])
        extract_wm_U = np.array([])
        extract_wm_V = np.array([])
        self.random_dct = np.random.RandomState(self.random_seed_dct)

        index = np.arange(self.block_shape[0] * self.block_shape[1])
        for i in range(self.length):
            self.random_dct.shuffle(index)
            wm_Y = self.block_get_wm(ha_Y_block[self.block_add_index0[i], self.block_add_index1[i]], index)
            wm_U = self.block_get_wm(ha_U_block[self.block_add_index0[i], self.block_add_index1[i]], index)
            wm_V = self.block_get_wm(ha_V_block[self.block_add_index0[i], self.block_add_index1[i]], index)
            wm = round((wm_Y + wm_U + wm_V) / 3)

            # else情况是对循环嵌入的水印的提取
            if i < self.wm_shape[0] * self.wm_shape[1]:
                extract_wm = np.append(extract_wm, wm)
                extract_wm_Y = np.append(extract_wm_Y, wm_Y)
                extract_wm_U = np.append(extract_wm_U, wm_U)
                extract_wm_V = np.append(extract_wm_V, wm_V)
            else:
                times = int(i / (self.wm_shape[0] * self.wm_shape[1]))
                ii = i % (self.wm_shape[0] * self.wm_shape[1])
                extract_wm[ii] = (extract_wm[ii] * times + wm) / (times + 1)
                extract_wm_Y[ii] = (extract_wm_Y[ii] * times + wm_Y) / (times + 1)
                extract_wm_U[ii] = (extract_wm_U[ii] * times + wm_U) / (times + 1)
                extract_wm_V[ii] = (extract_wm_V[ii] * times + wm_V) / (times + 1)

        wm_index = np.arange(extract_wm.size)
        self.random_wm = np.random.RandomState(self.random_seed_wm)
        self.random_wm.shuffle(wm_index)
        extract_wm[wm_index] = extract_wm.copy()
        extract_wm_Y[wm_index] = extract_wm_Y.copy()
        extract_wm_U[wm_index] = extract_wm_U.copy()
        extract_wm_V[wm_index] = extract_wm_V.copy()
        cv2.imwrite(out_wm_name, extract_wm.reshape(self.wm_shape[0], self.wm_shape[1]))

        path, file_name = os.path.split(out_wm_name)
        if not os.path.isdir(os.path.join(path, 'Y_U_V')):
            os.mkdir(os.path.join(path, 'Y_U_V'))
        cv2.imwrite(os.path.join(path, 'Y_U_V', 'Y' + file_name),
                    extract_wm_Y.reshape(self.wm_shape[0], self.wm_shape[1]))
        cv2.imwrite(os.path.join(path, 'Y_U_V', 'U' + file_name),
                    extract_wm_U.reshape(self.wm_shape[0], self.wm_shape[1]))
        cv2.imwrite(os.path.join(path, 'Y_U_V', 'V' + file_name),
                    extract_wm_V.reshape(self.wm_shape[0], self.wm_shape[1]))
