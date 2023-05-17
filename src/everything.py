#!/usr/bin/env python3

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image, CameraInfo
from cv_bridge import CvBridge
import cv2
import numpy as np

class imageSubscriber(Node):
    def __init__(self):

        super().__init__('image_subscriber')

        self.color_sub = self.create_subscription(
            Image,
            '/camera/color/image_raw',
            self.color_callback,
            10
        )

        self.depth_sub = self.create_subscription(
            Image,
            '/camera/depth/image_rect_raw',
            self.depth_callback,
            10,
        )

        self.info_sub = self.create_subscription(
            CameraInfo,
            '/camera/depth/camera_info',
            self.info_callback,
            10,
        )

        self.br = CvBridge()

        self.camera_info_pub = self.create_publisher(
            CameraInfo,
            '/cov_info',
            10
        )

        self.pothole_depth_publisher = self.create_publisher(
            Image,
            'pothole_depth',
            10
        )        # origianlly 'output_depth

        self.lane_depth_publisher = self.create_publisher(
            Image, 'lane_depth', 10
        )

        self.timer = self.create_timer(
            0.001,
            self.timer_callback  # publishing every 0.1 second
        )

        self.camera_info = CameraInfo()

        self.depth_image = np.zeros((720, 1280), np.uint16)
        self.pothole_depth = np.zeros((720, 1280), np.uint16)
        self.lanes = np.zeros((720, 1280), dtype='uint8')
        self.lane_depth = np.zeros((720, 1280), np.uint16)

    def info_callback(self, data):
        self.get_logger().info('Receiving camera info')
        self.camera_info = data
        # self.camera_info.header.stamp = self.get_clock().now().to_msg()
        # self.camera_info_pub.publish(self.camera_info)

    def timer_callback(self):
        pothole_depth_image = self.br.cv2_to_imgmsg(self.pothole_depth)
        lane_depth_image = self.br.cv2_to_imgmsg(self.lane_depth)

        time = self.get_clock().now().to_msg()

        pothole_depth_image.header.stamp = time
        lane_depth_image.header.stamp = time

        pothole_depth_image.header.frame_id = 'camera_link'
        lane_depth_image.header.frame_id = 'camera_link'

        self.camera_info.header.stamp = time

        self.pothole_depth_publisher.publish(pothole_depth_image)
        self.lane_depth_publisher.publish(lane_depth_image)
        self.camera_info_pub.publish(self.camera_info)

    def depth_callback(self, data):
        self.get_logger().info('Receiving depth frame')
        self.depth_image = self.br.imgmsg_to_cv2(data, 'passthrough')

    def color_callback(self, data):
        self.get_logger().info('Receiving color frame')

        self.color_image = self.br.imgmsg_to_cv2(data, 'bgr8')

        self.numpy_color_image = np.array(
            self.color_image
        )



        # --------------------------------- Color Filtering -------------------------------------------------------------

        self.hsv = cv2.cvtColor(self.color_image, cv2.COLOR_BGR2HSV)

        self.lower_white_hsv = (
            0,
            0,
            153     # Tune this value according to the environment
        )         # 0,0,200          0,0,168

        # Outside Main Building
        # 1. (0,0,138)
        # 2. (0,0,160)
        # 3. (0,0,181)      # most aggresive values
        # 4. (0,0,151)

        self.upper_white_hsv = (
            179,
            35,    # Tune this value according to the environment
            255,
        )     # 145,60,255       172,111,255

        # Outside Main Building
        # 1. (179,255,255)
        # 2. (179,60,255)
        # 3. (179,75,255)
        # 4. (179,43,255)

        self.hsv_color_mask = cv2.inRange(self.hsv, self.lower_white_hsv, self.upper_white_hsv)

        self.hsv_mask = cv2.bitwise_and(self.color_image, self.color_image, mask=self.hsv_color_mask)

        cv2.imshow('hsv', self.hsv_color_mask)

        # --------------------------------------------------------------------------------------------------------------



        # --------------------------------- rectangular region of interest ------------------------------------------------

        self.mask = np.zeros(self.hsv_color_mask.shape[:2], dtype='uint8')

        cv2.rectangle(self.mask, (0, 500), (1280, 720), (255, 0, 0), -1)
        
        self.masked_image = cv2.bitwise_and(self.hsv_color_mask, self.mask)     # total mask, HSV + ROI

        # -----------------------------------------------------------------------------------------------------------------


        # ---------------------------------- Part 1: Everything ----------------------------------------------------------------
        
        # self.masked_image = cv2.cvtColor(self.masked_image, cv2.COLOR_HSV2RGB)
        self.blurred = cv2.GaussianBlur(self.hsv_mask, (11, 11), 0)

        self.gray = cv2.cvtColor(self.blurred, cv2.COLOR_RGB2GRAY)

        ret, self.foreground = cv2.threshold(
            self.gray, 180, 200, cv2.THRESH_OTSU
        )

        self.se = cv2.getStructuringElement(cv2.MORPH_RECT, (11, 11))

        self.bg = cv2.morphologyEx(self.foreground, cv2.MORPH_DILATE, self.se)

        self.out_gray = cv2.divide(self.foreground, self.bg, scale=255)

        self.out_binary = cv2.threshold(self.out_gray, 180, 255, cv2.THRESH_OTSU)[1]

        cv2.imshow('Binary of Everything', self.out_binary)

        # ------------------------------------------------------------------------------------------------------------------

        # ----------------------------------- Part 2: Potholes -------------------------------------------------------------
        
        # detecting edges in the image using canny
        self.edges = cv2.Canny(self.blurred, 50, 150)
        
        # define a (3, 3) structuring element
        self.kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        
        # apply the dilation operation to the edged image
        self.dilated = cv2.dilate(self.edges, self.kernel, iterations=1)
        
        # find the contours in the edged image
        self.contours, self.hierarchy = cv2.findContours(self.edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        self.cnts = sorted(self.contours, key=cv2.contourArea, reverse=True)[:5]
                
        # ----------------------------------- bitwise_and on depth image with pothole mask ---------------------------
        
        self.potholes = np.zeros((720, 1280), dtype='uint8')
        self.pothole_depth = np.zeros((720, 1280), np.uint16)
        self.lanes = np.zeros((720, 1280), dtype='uint8')
        self.lane_depth = np.zeros((720, 1280), np.uint16)
        
        for contour in self.cnts:
        
            approx = cv2.approxPolyDP(contour, 0.01 * cv2.arcLength(contour, True), True)
            
            if len(approx) >= 8:
        
                self.potholes = cv2.fillPoly(self.potholes, pts=[contour], color=(255, 255, 255))
                print('Pothole Found')
        
        # General Erosion Kernel
        self.erosion_kernel = np.ones((3, 3), np.uint8)
        
        # Erosion of the pothole image to remove any background artifacts that come up
        self.eroded_pothole = cv2.erode(self.potholes, self.erosion_kernel, iterations=1)

        cv2.imshow('Potholes eroded', self.eroded_pothole)
        
        self.pothole_depth = cv2.bitwise_and(self.depth_image, self.depth_image, mask=self.eroded_pothole)

        print('Pothole Depth Published\n')
        
        # -------------------------------------------- Part3 : Lanes ---------------------------------------------

        self.xor_out = cv2.bitwise_xor(self.out_binary, self.potholes)
        
        cv2.imshow('XOR output', self.xor_out)

        self.erosion_kernel = np.ones((3, 3), np.uint8)
        self.eroded_xor_out = cv2.erode(self.xor_out, self.erosion_kernel, iterations=1)
        cv2.imshow('Erroded Output', self.eroded_xor_out)

        # finding and removing artefacts

        self.contours_2, self.hierarchy_2 = cv2.findContours(self.eroded_xor_out, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        
        cv2.imshow("contours_2", self.contours_2)
        
        for contour in self.contours_2:

            self.area = cv2.contourArea(contour)
            
            if self.area > 2000 and self.area < 8000:
                
                # Tune the above parameters according to real world testing
                print(self.area, '\n')
                self.lanes = cv2.fillPoly(self.lanes, pts=[contour], color=(255, 255, 255))
        
        print('Lanes Found')

        cv2.imshow('lanes', self.lanes)
        
        self.lane_depth = cv2.bitwise_and(self.depth_image, self.depth_image, mask=self.out_binary)

        print('Lane Depth Published\n')
        # --------------------------------------------------------------------------------------------------------

        cv2.waitKey(1)


def main(args=None):

    rclpy.init(args=args)

    image_subscriber = imageSubscriber()

    rclpy.spin(image_subscriber)


if __name__ == '__main__':
    main()